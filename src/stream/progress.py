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
    # 定点重生成节点（仅用于成本/日志展示，不进流水线步骤条）
    "page_regen": "单页重写", "asset_regen": "定点重生图",
}

# 流水线步骤条顺序（13 节点；重生成节点不在其中）
NODE_ORDER = [
    "task_import", "entity_bind", "evidence_build", "draft_gen", "rule_check",
    "page_split", "asset_gen", "ocr_read", "cross_check", "risk_classify",
    "review_queue", "batch_signoff", "publish_snapshot",
]


def _done_msg(node: str, data: dict) -> str:
    if node == "draft_gen":
        return f"模型 {data.get('model', '')} · {data.get('length', 0)}字"
    if node == "asset_gen":
        return f"{data.get('count', 0)}张图"
    if node == "evidence_build":
        return f"证据 {data.get('evidence_count', 0)}条" + (" · ⚠争议" if data.get("conflicts") else "")
    if node == "risk_classify":
        return f"风险 {data.get('level', '')}"
    if node == "entity_bind":
        return f"搜图 {data.get('searched_images', 0)}张"
    return "完成"


class ProgressTracker:
    def __init__(self):
        self.tasks: dict[str, dict] = {}
        self.counts = {"queued": 0, "processing": 0, "done": 0, "failed": 0}
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
                "model": "", "error": "", "debug": [],
            }
            self.counts["queued"] += 1
        elif etype == "task_started" and t:
            t["status"] = "processing"
            self.counts["queued"] = max(0, self.counts["queued"] - 1)
            self.counts["processing"] += 1
        elif etype == "task_finished" and t:
            t["status"] = "done"
            self.counts["processing"] = max(0, self.counts["processing"] - 1)
            self.counts["done"] += 1
        elif etype == "task_failed" and t:
            t["status"] = "failed"
            t["error"] = data.get("error", "")
            self.counts["processing"] = max(0, self.counts["processing"] - 1)
            self.counts["failed"] += 1
        elif etype == "node_started" and t:
            t["current_node"] = data.get("node", "")
        elif etype == "node_finished" and t:
            node = data.get("node", "")
            if node and node not in t["nodes"]:
                t["nodes"].append(node)
            t["current_node"] = node
            t.setdefault("debug", []).append({
                "ts": datetime.now().strftime("%H:%M:%S"),
                "node": node, "label": NODE_LABEL.get(node, node),
                "phase": "done", "elapsed": data.get("elapsed"),
                "msg": _done_msg(node, data),
            })
            if node == "draft_gen" and data.get("preview"):
                t["preview"] = data["preview"]
                t["model"] = data.get("model", "")
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
            t.setdefault("debug", []).append({
                "ts": datetime.now().strftime("%H:%M:%S"),
                "node": node, "label": NODE_LABEL.get(node, node),
                "phase": "error", "elapsed": data.get("elapsed"),
                "msg": data.get("error", ""),
                "trace": data.get("traceback", ""),
            })

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
            "node_order": NODE_ORDER,
        }

    def clear(self) -> None:
        """清空任务进度状态（配合后台删除工作内容）。"""
        self.tasks.clear()
        self.counts = {"queued": 0, "processing": 0, "done": 0, "failed": 0}

    def get_log(self) -> list[str]:
        return list(self.log)


progress = ProgressTracker()
