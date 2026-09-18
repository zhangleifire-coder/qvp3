"""实时进度追踪器：订阅事件总线，维护每个任务的节点进度、内容与运行日志。"""
import asyncio
from datetime import datetime

from src.stream.bus import bus

NODE_LABEL = {
    "task_import": "任务导入", "entity_bind": "搜实景图", "evidence_build": "证据+交叉验证",
    "draft_gen": "正文生成", "rule_check": "规则质检", "page_split": "分页文案",
    "asset_gen": "配图生成", "ocr_read": "OCR回读", "cross_check": "图文一致性",
    "risk_classify": "风险分流", "review_queue": "审核队列", "batch_signoff": "批次会签",
    "publish_snapshot": "发布快照",
    # 创作 Agent 路径（2026-08-22 改造；网关为 dsh_serve）
    "agent_production": "创作Agent生产",
    # 定点重生成节点（仅用于成本/日志展示，不进流水线步骤条）
    "page_regen": "单页重写", "asset_regen": "定点重生图",
}

# 流水线步骤条顺序（13 节点；重生成节点不在其中）
NODE_ORDER = [
    "task_import", "entity_bind", "evidence_build", "draft_gen", "rule_check",
    "page_split", "asset_gen", "ocr_read", "cross_check", "risk_classify",
    "review_queue", "batch_signoff", "publish_snapshot",
]

# 创作大节点路径（11 节点）：创作段六节点收敛为 agent_production；
# 2026-09-07 两段式实景搜图：ref_seed（搜图①自动，反哺起草）在 text_check
# 之前，ref_collect（搜图②叠加+人工确认关）在 text_check 之后
NODE_ORDER_AGENT = [
    "task_import", "ref_seed", "text_check", "ref_collect", "agent_production",
    "rule_check", "cross_check", "risk_classify", "review_queue",
    "batch_signoff", "publish_snapshot",
]
NODE_LABEL["ref_seed"] = "搜实景图·创作参考"
NODE_LABEL["ref_collect"] = "参考图确认"
NODE_LABEL["text_check"] = "文字自查"

# staged 分阶段 Agent 路径（14 节点，2026-09-09）：agent_production 拆为
# agent_evidence（取证+风格判定）→ agent_draft（正文）→ agent_pages（分页）
# → agent_assets（配图生成）4 个独立 Agent 节点，成本/耗时按阶段拆分可见
NODE_ORDER_AGENT_STAGED = [
    "task_import", "ref_seed", "text_check", "ref_collect",
    "agent_evidence", "agent_draft", "agent_pages", "agent_assets",
    "rule_check", "cross_check", "risk_classify", "review_queue",
    "batch_signoff", "publish_snapshot",
]
NODE_LABEL["agent_evidence"] = "取证+风格判定"
NODE_LABEL["agent_draft"] = "正文创作"
NODE_LABEL["agent_pages"] = "分页文案"
NODE_LABEL["agent_assets"] = "配图生成"

# 快照有界化（性能优化 P0-3）：终态任务只留最近 N 条供 debug 回看，
# 进行中/挂起（awaiting_text/awaiting_refs 等人审关）永不淘汰；
# 每任务 debug 只留最近 M 条（含 traceback）。内存与 /api/stream/state
# payload 由此封顶，不再随运行时长无界增长。
TERMINAL_KEEP = 30
DEBUG_KEEP = 20
_ACTIVE_STATUSES = {"queued", "processing", "awaiting_refs", "awaiting_text"}


def node_order() -> list:
    """当前生效的流水线步骤条顺序（AGENT_PIPELINE_ENABLED 双路径 +
    AGENT_PIPELINE_VARIANT staged/monolith 分发）。"""
    from src.config import settings
    if settings.agent_pipeline_enabled:
        if settings.agent_pipeline_variant == "staged":
            return NODE_ORDER_AGENT_STAGED
        return NODE_ORDER_AGENT
    return NODE_ORDER


def _done_msg(node: str, data: dict) -> str:
    if node in ("draft_gen", "agent_draft"):
        return f"模型 {data.get('model', '')} · {data.get('length', 0)}字"
    if node in ("asset_gen", "agent_assets"):
        return f"{data.get('count', data.get('asset_count', 0))}张图"
    if node == "agent_evidence":
        parts = [f"证据{data.get('evidence_count', 0)}条"]
        if data.get("image_style"):
            parts.append(f"风格{data['image_style']}")
        if data.get("correction_rounds"):
            parts.append(f"纠错{data['correction_rounds']}轮")
        return " · ".join(parts)
    if node == "agent_pages":
        return f"{data.get('page_count', 0)}页文案" + (
            f" · 纠错{data['correction_rounds']}轮" if data.get("correction_rounds") else "")
    if node == "evidence_build":
        return f"证据 {data.get('evidence_count', 0)}条" + (" · ⚠争议" if data.get("conflicts") else "")
    if node == "agent_production":
        parts = [f"{data.get('length', 0)}字", f"{data.get('asset_count', 0)}张图",
                 f"证据{data.get('evidence_count', 0)}条"]
        if data.get("correction_rounds"):
            parts.append(f"纠错{data['correction_rounds']}轮")
        return " · ".join(parts)
    if node == "risk_classify":
        return f"风险 {data.get('level', '')}"
    if node == "entity_bind":
        return f"搜图 {data.get('searched_images', 0)}张"
    return "完成"


def _tool_stage_text(data: dict) -> str:
    """Agent 工具调用 → 可读阶段文案（监控页展示当前创作子阶段）。"""
    tool = data.get("tool", "")
    page, total = data.get("page"), data.get("total")
    if tool == "web_search":
        return f"检索证据（{data.get('count', '')} 条结果）".replace("（）", "")
    if tool == "image_search":
        return "搜索实景参考图"
    if tool == "image_gen_progress":
        return f"生成配图 P{page}/{total}"
    if tool == "image_gen":
        return f"配图生成完成（共 {data.get('pages', total or '')} 张）".replace("（）", "")
    if tool == "ocr":
        return "OCR 图文自检"
    return f"调用工具 {tool}"


class ProgressTracker:
    def __init__(self):
        self.tasks: dict[str, dict] = {}
        self.counts = {"queued": 0, "processing": 0, "done": 0, "failed": 0,
                       "cancelled": 0}
        self.log: list[str] = []
        self._q: asyncio.Queue | None = None
        self._consumer: asyncio.Task | None = None

    async def start(self) -> None:
        if self._q is None:
            self._q = bus.subscribe()
            self._consumer = asyncio.create_task(self._consume())

    async def stop(self) -> None:
        if self._consumer:
            self._consumer.cancel()
            self._consumer = None
        if self._q is not None:
            bus.unsubscribe(self._q)
            self._q = None

    async def _consume(self) -> None:
        while True:
            try:
                event = await self._q.get()
            except asyncio.CancelledError:
                break
            self._handle(event)

    def _handle(self, event: dict) -> None:
        etype = event["type"]
        tid = event.get("task_id")
        data = event.get("data", {})
        self._append_log(etype, tid, data)
        t = self.tasks.get(tid) if tid else None

        if etype == "task_enqueued":
            self.tasks[tid] = {
                "id": tid, "query": data.get("query", ""), "status": "queued",
                "nodes": [], "current_node": "", "preview": "", "imgs": [],
                "model": "", "error": "", "debug": [], "node_streams": {},
            }
            self.counts["queued"] += 1
            self._evict_terminal()
        elif etype == "task_started" and t:
            t["status"] = "processing"
            self.counts["queued"] = max(0, self.counts["queued"] - 1)
            self.counts["processing"] += 1
        elif etype == "task_finished" and t:
            t["status"] = "done"
            self.counts["processing"] = max(0, self.counts["processing"] - 1)
            self.counts["done"] += 1
            self._evict_terminal()
        elif etype == "task_failed" and t:
            t["status"] = "failed"
            t["error"] = data.get("error", "")
            self.counts["processing"] = max(0, self.counts["processing"] - 1)
            self.counts["failed"] += 1
            self._evict_terminal()
        elif etype == "task_cancelled" and t:
            t["status"] = "cancelled"
            t["error"] = ""
            self.counts["processing"] = max(0, self.counts["processing"] - 1)
            self.counts["queued"] = max(0, self.counts["queued"] - 1)
            self._evict_terminal()
        elif etype == "node_started" and t:
            t["current_node"] = data.get("node", "")
            # 节点（重）开始：重置该节点过程流帧，去掉上一轮残留
            node = data.get("node", "")
            if node:
                t.setdefault("node_streams", {})[node] = {
                    "node": node, "chars": 0, "preview": "", "msg": "",
                    "ts": datetime.now().strftime("%H:%M:%S")}
        elif etype == "node_progress" and t:
            node = data.get("node", "")
            if node:
                self._merge_stream_frame(t, node, data)
        elif etype == "node_finished" and t:
            node = data.get("node", "")
            if node and node not in t["nodes"]:
                t["nodes"].append(node)
            t["current_node"] = node
            self._push_debug(t, {
                "ts": datetime.now().strftime("%H:%M:%S"),
                "node": node, "label": NODE_LABEL.get(node, node),
                "phase": "done", "elapsed": data.get("elapsed"),
                "msg": _done_msg(node, data),
            })
            if node == "draft_gen" and data.get("preview"):
                t["preview"] = data["preview"]
                t["model"] = data.get("model", "")
            elif node == "agent_production" and data.get("preview"):
                t["preview"] = data["preview"]
                t["model"] = data.get("model", "")
                if data.get("image_urls"):
                    t["imgs"] = data["image_urls"]
                t.pop("stream", None)      # 创作完成：收起流式输出框
                t.pop("stage_hint", None)
            elif node == "agent_draft" and data.get("preview"):
                # staged 路径：正文在 agent_draft 阶段产出
                t["preview"] = data["preview"]
                t["model"] = data.get("model", "")
            elif node == "agent_assets" and data.get("image_urls"):
                # staged 路径：配图在 agent_assets 阶段产出，收起流式输出框
                t["imgs"] = data["image_urls"]
                t.pop("stream", None)
                t.pop("stage_hint", None)
            elif node == "asset_gen" and data.get("image_urls"):
                t["imgs"] = data["image_urls"]
            elif node == "risk_classify" and data.get("level"):
                reasons = data.get("reasons", [])
                t["preview"] = "风险：" + data["level"] + (" · " + "；".join(reasons) if reasons else "")
            elif node == "evidence_build":
                t["preview"] = "证据 " + str(data.get("evidence_count", 0)) + " 条"
                t["conflicts"] = data.get("conflicts", [])
        elif etype == "node_failed" and t:
            node = data.get("node", "")
            t["current_node"] = node
            self._push_debug(t, {
                "ts": datetime.now().strftime("%H:%M:%S"),
                "node": node, "label": NODE_LABEL.get(node, node),
                "phase": "error", "elapsed": data.get("elapsed"),
                "msg": data.get("error", ""),
                "trace": data.get("traceback", ""),
            })
        elif etype == "agent_progress" and t:
            # 创作 Agent 流式输出：保留最新流状态（监控页流式框），
            # debug 只在开始/每千字记一条，避免刷屏。
            # 归属当前执行节点：monolith=agent_production，staged=各阶段节点
            cur = t.get("current_node") or "agent_production"
            t["stream"] = {"chars": data.get("chars", 0),
                           "tokens": data.get("tokens_est", 0),
                           "tail": data.get("preview", "")}
            self._merge_stream_frame(t, cur, {
                "chars": data.get("chars"), "preview": data.get("preview"),
                "msg": data.get("message", "")})
            chars = data.get("chars", 0)
            if chars and (chars - t.get("_stream_logged", 0)) >= 1000:
                t["_stream_logged"] = chars
                self._push_debug(t, {
                    "ts": datetime.now().strftime("%H:%M:%S"),
                    "node": cur, "label": NODE_LABEL.get(cur, cur),
                    "phase": "running", "elapsed": None,
                    "msg": f"流式输出中… {chars} 字（约 {data.get('tokens_est', 0)} token）",
                })
        elif etype == "agent_tool" and t:
            # Agent 工具调用阶段：更新阶段提示（检索证据/生成配图 P3/6/OCR自检）
            cur = t.get("current_node") or "agent_production"
            t["stage_hint"] = _tool_stage_text(data)
            self._push_debug(t, {
                "ts": datetime.now().strftime("%H:%M:%S"),
                "node": cur, "label": NODE_LABEL.get(cur, cur),
                "phase": "running", "elapsed": None,
                "msg": t["stage_hint"],
            })

    def _push_debug(self, t: dict, entry: dict) -> None:
        """追加 debug 行并按 DEBUG_KEEP 截断（traceback 随旧行一起淘汰）。"""
        dbg = t.setdefault("debug", [])
        dbg.append(entry)
        if len(dbg) > DEBUG_KEEP:
            del dbg[:-DEBUG_KEEP]

    def _evict_terminal(self) -> None:
        """终态任务只保留最近 TERMINAL_KEEP 条（按入队先后淘汰最旧）；
        进行中/挂起任务永不淘汰。counts 是累计计数器，不受淘汰影响。"""
        terminal = [tid for tid, t in self.tasks.items()
                    if t.get("status") not in _ACTIVE_STATUSES]
        excess = len(terminal) - TERMINAL_KEEP
        if excess > 0:
            for tid in terminal[:excess]:
                del self.tasks[tid]

    def _merge_stream_frame(self, t: dict, node: str, data: dict) -> None:
        """node_streams 帧按字段合并：msg 帧不冲掉流式 chars/preview，反之亦然。"""
        frame = t.setdefault("node_streams", {}).setdefault(node, {
            "node": node, "chars": 0, "preview": "", "msg": "", "ts": ""})
        if data.get("msg"):
            frame["msg"] = data["msg"]
        if data.get("chars"):
            frame["chars"] = data["chars"]
        if data.get("preview"):
            frame["preview"] = data["preview"][-300:]
        frame["ts"] = datetime.now().strftime("%H:%M:%S")

    def _append_log(self, etype: str, tid, data: dict) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        short = (tid[:8] + "…") if tid else "-"
        node = data.get("node", "")
        label = NODE_LABEL.get(node, node)
        if etype == "task_enqueued":
            line = f"[{ts}] 入队    {short}  {data.get('query', '')}"
        elif etype == "task_started":
            line = f"[{ts}] 开始    {short}  {data.get('query', '')}"
        elif etype == "task_finished":
            line = f"[{ts}] 完成    {short}"
        elif etype == "task_failed":
            line = f"[{ts}] 失败    {short}  {data.get('error', '')[:100]}"
        elif etype == "node_started":
            line = f"[{ts}] 进入步骤 {short}  {label}"
        elif etype == "node_failed":
            el = f"（{data.get('elapsed', 0)}s）" if data.get("elapsed") else ""
            line = f"[{ts}] ✘ 步骤失败 {short}  {label}{el}  {data.get('error', '')[:120]}"
        elif etype == "node_finished":
            extra = ""
            if node == "draft_gen":
                extra = f"（{data.get('length', 0)}字）"
            elif node == "asset_gen":
                extra = f"（{data.get('count', 0)}张）"
            elif node == "risk_classify":
                extra = f"（{data.get('level', '')}）"
            line = f"[{ts}] 完成步骤 {short}  {label}{extra}"
        elif etype == "rate_limit":
            line = f"[{ts}] ⚠ 限流    并发降至 {data.get('capacity', '')}"
        elif etype == "concurrency":
            line = f"[{ts}] 并发调整 {data.get('previous', '')}→{data.get('capacity', '')}"
        elif etype == "maintenance":
            line = f"[{ts}] 周期切换 {data.get('mode', '')}（{data.get('reason', '')}）"
        else:
            line = f"[{ts}] {etype}  {short}"
        self.log.append(line)
        if len(self.log) > 3000:
            self.log = self.log[-3000:]

    def snapshot(self) -> dict:
        return {
            "counts": dict(self.counts),
            "tasks": list(self.tasks.values()),
            "node_order": node_order(),
        }

    def clear(self) -> None:
        """清空任务进度状态（配合后台删除工作内容）。"""
        self.tasks.clear()
        self.counts = {"queued": 0, "processing": 0, "done": 0, "failed": 0,
                       "cancelled": 0}

    def get_log(self) -> list[str]:
        return list(self.log)


progress = ProgressTracker()
