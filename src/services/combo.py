"""组合生成导入的公共服务：泛化问题池解析 / 随机抽取 / 风格提示映射。

设计（对应导入设计表）：
- 一行导入 = 原始 query（长情境）+ 泛化问题池（逗号/分号/顿号/换行分隔，可由
  LLM 智能分析自动补充）+ 风格条件 + 垂类条件 + 生成次数 N；
- 导入时从池中随机抽 N 个问题（池够大时不重复），每个与原始 query 组合成
  一条生产任务：query=抽中的问题（列表可读），source_query=原始情境，
  风格/垂类随任务落库并在 agent_production 提示词中注入。
"""
import json
import random
import re
from pathlib import Path

# 问题池分隔符：中英文逗号/分号/顿号/换行
_SPLIT_RE = re.compile(r"[，,；;、\n]+")


def parse_pool(pool: str | list[str]) -> list[str]:
    """把问题池文本（或列表）解析为去空去重的问题列表。"""
    if isinstance(pool, list):
        raw_items = [str(p).strip() for p in pool]
    else:
        raw_items = [p.strip() for p in _SPLIT_RE.split(pool or "")]
    seen, items = set(), []
    for p in raw_items:
        if p and p not in seen:
            seen.add(p)
            items.append(p)
    return items


def pick_rounds(pool: list[str], rounds: int, rng: random.Random | None = None) -> list[str]:
    """从问题池随机抽 rounds 个问题（返回列表长度 == rounds）。

    池 >= rounds：random.sample 不重复抽取；
    池 <  rounds：先全量洗牌一遍，再循环补齐（保证任务数始终等于要的条数）。
    """
    rng = rng or random
    rounds = max(1, min(int(rounds or 1), 50))
    if not pool:
        return []
    if len(pool) >= rounds:
        return rng.sample(pool, rounds)
    picked = pool[:]
    rng.shuffle(picked)
    while len(picked) < rounds:
        picked.append(pool[len(picked) % len(pool)])
    return picked[:rounds]


# 已知风格 → 创作提示（自由文本风格原样透传给 Agent）
STYLE_HINTS = {
    "解读·经验分享": "以亲历者/过来人口吻做经验分享式解读，结合真实使用场景讲感受",
    "经验分享": "以亲历者/过来人口吻做经验分享式解读，结合真实使用场景讲感受",
    "解读": "客观拆解原理与关键点，讲清「为什么」，帮读者看懂门道",
    "测评实测": "以实测对比视角呈现，讲数据、讲差异、讲适用人群",
    "攻略教程": "步骤化教程口吻，按「第一步/第二步…」组织，可直接照做",
    "避坑指南": "问题+避坑清单式：先讲常见坑，再逐条给规避办法",
    "观点杂谈": "观点论述口吻：亮明观点，摆事实讲逻辑，允许有态度",
}


def style_hint(style: str | None) -> str:
    """风格名 → 创作要求；未知风格原样作为写作风格要求注入。"""
    if not style:
        return ""
    return STYLE_HINTS.get(style.strip(), f"按「{style.strip()}」的风格要求行文")


# 图片整体视觉风格库：Agent 按内容气质自适应选择（多适配随机选），
# 描述词会替换生图模板中的固定风格句。
# WS3 单一事实来源（2026-09-11）：公共风格权威源是 data/styles.json
# （scripts/sync_styles.py 同步进 DB 公共库），此处启动时读同一文件做
# 代码兜底；读不到/为空才用内联最小兜底 1 条。公共库改动一律改 json，
# 不要在这里增删条目。
_STYLES_JSON = Path(__file__).resolve().parents[2] / "data" / "styles.json"

# 内联最小兜底（仅 styles.json 缺失/损坏时生效，保证服务可启动）
_FALLBACK_STYLE_LIBRARY = [
    ("自然写实暖调", "奶油米/浅米黄低饱和暖底、写实摄影主体、柔和自然光、浅景深、深棕字压米底、衬线宋体或黑体大标题、留白约五成，像人工精修的杂志级卡片"),
]


def load_style_entries(path: Path | None = None) -> list[dict]:
    """读 data/styles.json 返回启用条目（含 keywords/use_when/pitfalls 全字段）；
    读不到或为空返回 []（调用方回退 _FALLBACK_STYLE_LIBRARY）。"""
    try:
        data = json.loads((path or _STYLES_JSON).read_text(encoding="utf-8"))
        return [s for s in data.get("styles", [])
                if s.get("enabled", True) and str(s.get("style_name", "")).strip()]
    except Exception:  # noqa: BLE001
        return []


def _load_image_style_library() -> list[tuple[str, str]]:
    entries = load_style_entries()
    if entries:
        return [(s["style_name"].strip(), str(s.get("description", "")).strip())
                for s in entries]
    return list(_FALLBACK_STYLE_LIBRARY)


IMAGE_STYLE_LIBRARY = _load_image_style_library()

CONTENT_STYLE_LIBRARY = ["解读·经验分享", "测评实测", "攻略教程", "避坑指南", "观点杂谈"]


def image_style_library_text() -> str:
    """风格库渲染成提示词里的清单文本。"""
    return "\n".join(f"- {name}：{desc}" for name, desc in IMAGE_STYLE_LIBRARY)


def analyze_prompt(query: str, count: int = 20) -> str:
    """智能分析：从原始 query 生成泛化补充问题池的 LLM 提示词。

    模板单点在 skills/prompt-analyze/SKILL.md（2026-09-03 阶段2重构，原样搬运）。
    """
    from src.gateway.skill_loader import skill_body
    return skill_body("prompt-analyze").format(count=count, query=query.strip())


def parse_analyzed_questions(text: str) -> list[str]:
    """解析 LLM 返回的编号列表为问题列表（容错：围栏/寒暄尾行/无编号行）。"""
    numbered: list[str] = []
    plain: list[str] = []
    for ln in (text or "").strip().splitlines():
        ln = ln.strip().strip("`").lstrip("-*•").strip()
        if not ln:
            continue
        m = re.match(r"^\d{1,3}[\.、）)]:?]?\s*(.+)$", ln)
        if m:
            numbered.append(m.group(1).strip())
        else:
            plain.append(ln)
    # 有编号行时只信编号行（过滤模型尾部的寒暄/总结句），否则退回短行
    items = numbered if numbered else plain
    return parse_pool([it for it in items if 2 <= len(it) <= 60])
