"""结构化分页文案（v0.1.4 P2，模板《生图通用Prompt模板-20260922》场景A schema）。

每页 PageSpec：
    {"title":     ≤18字短句标题（原样上图）
     "subtitle":  6-28字胶囊副题（可空）
     "points":    [≤4条 × ≤18字要点]（原样上图）
     "subject":   ≤20字具体可画名词短语（喂插图提示做主体锚定）
     "info_task": 一句话信息任务（跨页信息去重审计用）}

链路：page-split/Agent 输出结构化 JSON → parse_pages_json 校验收敛 →
task.page_specs 快照 → compose 直连消费（subject 进 sub_prompts）。
兼容层：spec_from_plain 包装 poster_compose.split_title_points（旧任务/
解析回退的纯文本页 → 结构化，subject 留空由 ill_prompt 兜底）。

上图文字量契约不变：render_page_text(spec) 仍须落在
contract.txt 的 80-130 字/页、任意两页差 ≤40（沿用 page_balance_issue 校验）。
"""
from __future__ import annotations

TITLE_MAX = 18
SUBTITLE_MIN, SUBTITLE_MAX = 6, 28
POINT_MAX = 18
POINTS_LIMIT = 4
SUBJECT_MAX = 20

# 5/6 页页面结构（模板 §3.2：5 页去掉清单页，其余角色不变）
_PAGE_PLAN_6 = (
    "   第 1 页封面 = 主标题（12-20字）+ 一句钩子 + 两行辅助说明；\n"
    "   第 2-5 页每页围绕一个核心信息点讲透 = 小标题 + 4-6 句干货；\n"
    "   第 6 页结尾 = 一句总结 + 适合谁/行动建议 + 一句补充。"
)
_PAGE_PLAN_5 = (
    "   第 1 页封面 = 主标题（12-20字）+ 一句钩子 + 两行辅助说明；\n"
    "   第 2-4 页每页围绕一个核心信息点讲透 = 小标题 + 4-6 句干货；\n"
    "   第 5 页结尾 = 一句总结 + 适合谁/行动建议 + 一句补充。"
)


def page_plan_text(n: int) -> str:
    return _PAGE_PLAN_5 if n == 5 else _PAGE_PLAN_6


def fill_page_template(tpl: str, n: int) -> str:
    """把 page-split 模板里的 {page_count}/{page_plan} 占位符按页数填充。"""
    return (tpl.replace("{page_count}", str(n))
               .replace("{page_plan}", page_plan_text(n)))


def _clip(s, limit: int) -> str:
    s = (s or "").strip()
    return s[:limit]


def normalize_spec(obj) -> dict:
    """单个页对象 → 收敛后的 PageSpec；非 dict / 无 title 抛 ValueError。"""
    if not isinstance(obj, dict):
        raise ValueError("页对象必须是 JSON 对象")
    title = _clip(obj.get("title"), TITLE_MAX)
    if not title:
        raise ValueError("页对象缺少非空 title")
    subtitle = _clip(obj.get("subtitle"), SUBTITLE_MAX)
    if subtitle and len(subtitle) < SUBTITLE_MIN:
        subtitle = ""          # 过短副题不如没有（胶囊排版最低要求）
    points_raw = obj.get("points")
    points = []
    if isinstance(points_raw, list):
        for p in points_raw:
            p = _clip(p, POINT_MAX)
            if p:
                points.append(p)
            if len(points) >= POINTS_LIMIT:
                break
    return {
        "title": title,
        "subtitle": subtitle,
        "points": points,
        "subject": _clip(obj.get("subject"), SUBJECT_MAX),
        "info_task": _clip(obj.get("info_task"), 60),
    }


def parse_pages_json(raw: str, expected_n: int = 6) -> list[dict] | None:
    """解析 LLM 分页输出（结构化对象数组，容错围栏）。

    返回 PageSpec 列表；任何硬失败（非 JSON / 长度不符 / 页对象非法 /
    模型退化为纯字符串数组）返回 None——调用方走兼容层或重试。
    """
    import json as _json
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()
    try:
        start, end = text.index("["), text.rindex("]") + 1
        arr = _json.loads(text[start:end])
    except Exception:
        return None
    if not isinstance(arr, list) or len(arr) != expected_n:
        return None
    specs = []
    try:
        for item in arr:
            specs.append(normalize_spec(item))
    except Exception:
        return None
    return specs


def render_page_text(spec: dict) -> str:
    """PageSpec → 上图纯文本（page_copies.body 口径：标题/副题/要点逐行）。"""
    lines = [spec["title"]]
    if spec.get("subtitle"):
        lines.append(spec["subtitle"])
    lines.extend(spec.get("points") or [])
    return "\n".join(lines)


def spec_from_plain(body: str) -> dict:
    """兼容层：纯文本页 → PageSpec（split_title_points 机械拆分，
    subject 留空——由调用方 ill_prompt 兜底，不伪造主体）。"""
    from src.services.poster_compose import split_title_points
    title, subtitle, points = split_title_points(body or "")
    return {"title": title or (body or "").strip()[:TITLE_MAX],
            "subtitle": subtitle, "points": points,
            "subject": "", "info_task": ""}


def specs_from_plain_pages(pages: list) -> list[dict]:
    return [spec_from_plain(p) for p in pages]


def specs_from_rendered(specs: list[dict]) -> list[str]:
    return [render_page_text(s) for s in specs]
