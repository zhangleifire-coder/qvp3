"""组合生成导入的公共服务：泛化问题池解析 / 随机抽取 / 风格提示映射。

设计（对应导入设计表）：
- 一行导入 = 原始 query（长情境）+ 泛化问题池（逗号/分号/顿号/换行分隔，可由
  LLM 智能分析自动补充）+ 风格条件 + 垂类条件 + 生成次数 N；
- 导入时从池中随机抽 N 个问题（池够大时不重复），每个与原始 query 组合成
  一条生产任务：query=抽中的问题（列表可读），source_query=原始情境，
  风格/垂类随任务落库并在 agent_production 提示词中注入。
"""
import random
import re

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
# 描述词会替换生图模板中的固定风格句（坚韧治愈风、高清、极简高级）。
IMAGE_STYLE_LIBRARY = [
    ("治愈暖彩", "柔和暖色调插画风、圆润造型、充足留白、治愈呼吸感、高清"),
    ("真实摄影", "真实实拍质感、自然光影、浅景深、生活化场景、高清细节"),
    ("扁平极简", "扁平矢量插画、大色块几何构图、低饱和配色、极简大量留白"),
    ("3D渲染", "C4D 三维质感、柔和材质光泽、轻拟物造型、明快渐变背景"),
    ("手绘线稿", "手绘钢笔线稿加水彩淡彩、纸张肌理、自然笔触感"),
    ("杂志编辑", "时尚杂志编辑排版、衬线大标题、高级灰底色、克制配色"),
    ("信息图表", "图标化信息呈现、图表化数据可视化、清晰导视结构"),
    ("复古印刷", "米色纸底、复古双色调印刷、噪点肌理、旧海报质感"),
]

CONTENT_STYLE_LIBRARY = ["解读·经验分享", "测评实测", "攻略教程", "避坑指南", "观点杂谈"]


def image_style_library_text() -> str:
    """风格库渲染成提示词里的清单文本。"""
    return "\n".join(f"- {name}：{desc}" for name, desc in IMAGE_STYLE_LIBRARY)


def analyze_prompt(query: str, count: int = 20) -> str:
    """智能分析：从原始 query 生成泛化补充问题池的 LLM 提示词。"""
    return f"""你是内容策划助手。针对下面的用户原始提问，生成 {count} 个可独立成文的「泛化补充问题」——围绕该提问情境、适合做成图文内容的相邻角度问题，后续每个问题都会与原始提问组合生成一篇内容。

要求：
- 每个问题 8-20 字，口语化、有真实搜索感（像用户会在搜索框里敲的问题）
- 角度多元：价格费用 / 怎么选 / 避坑 / 对比 / 保养维修 / 经验心得 / 适用人群等
- 不与原始提问完全重复；问题之间不重复
- 只输出编号列表（1. 2. 3. …），每行一个，不要任何其它内容

【原始提问】
{query.strip()}"""


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
