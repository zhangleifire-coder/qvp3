"""提示词版本库：按 (用途, mode) 提供正文/生图提示词。

2026-09-03 阶段2重构：全部提示词正文搬入 skills/ 包（单一事实来源），
本文件常量从 src.gateway.skill_loader 读取，名称与取值保持不变；
三级解析（用户自定义 → admin 覆盖 → 代码默认）语义不变。

资产映射：
- draft-write/    → DRAFT_PROMPTS / _DRAFT_SHARED / DRAFT_POLISH_PROMPT
- page-split/     → PAGES_PROMPT（SKILL.md 正文；字数契约 contract.txt）
- image-prompt/   → IMAGE_PROMPTS / IMAGE_PROMPTS_EN / _SHARED_IMAGE_STYLE
                    / _SUBJECT_ANCHOR / _IMAGE_CONSTRAINTS_EN / _PAGE_LAYOUTS(_EN)
- page-regen/     → PAGE_REGEN_PROMPT
"""
from src.gateway import skill_loader as _skills

# 生图硬约束底座（风格无关）：色调/字体/质感由「本篇视觉风格」段落控制
#（演进史见 skills/image-prompt/SKILL.md notes）
_SHARED_IMAGE_STYLE = _skills.fragment("image-prompt", "shared_style")

# 通用主体锚定句（_SHARED_IMAGE_STYLE 的锚定子串，_apply_page_subject 替换用）
_SUBJECT_ANCHOR = _skills.fragment("image-prompt", "subject_anchor")

# 英文生图骨架（2026-09-01 场景化扩写链路）：英文约束底座 + 英文布局轮换 +
# 三模式英文题材前缀（详见 skills/image-prompt/SKILL.md）
_IMAGE_CONSTRAINTS_EN = _skills.fragment("image-prompt", "constraints_en")

_PAGE_LAYOUTS_EN = _skills.fragment_list("image-prompt", "layouts_en")

IMAGE_PROMPTS_EN = {
    mode: _skills.fragment("image-prompt", f"prompts_en/{mode}")
    for mode in ("general", "single", "compare")
}


def _apply_page_subject(prompt: str, page_subject: str = None) -> str:
    """动态主体锚定（2026-08-31 移植 8002）：asset_gen 已从本页文案提取画面主体时，
    把通用锚定句替换为该主体句；无主体/模板不含锚定句则原样返回。"""
    subject = (page_subject or "").strip()
    if subject and _SUBJECT_ANCHOR in prompt:
        prompt = prompt.replace(
            _SUBJECT_ANCHOR,
            f"（主体锚定）本页画面主体必须是：{subject}，占据画面视觉中心；"
            f"风格描述词仅作光影色调氛围，"
            f"严禁用与本页文案无关的象征隐喻物替代主体。")
    return prompt


DRAFT_PROMPTS = {
    mode: _skills.mode_fragment("draft-write", mode)
    for mode in ("general", "single", "compare")
}

# 正文人设化共享段：追加到各模式 DRAFT_PROMPTS 之后（仅系统默认模板，
# 用户自定义模板代表显式意图不覆盖）；演进史见 skills/draft-write/SKILL.md
_DRAFT_SHARED = _skills.fragment("draft-write", "shared_persona")

# 校稿润色：draft_gen 节点内创作后二段执行，失败/过短沿用原稿（700 字上限口径）
DRAFT_POLISH_PROMPT = _skills.fragment("draft-write", "polish")

# text_check 起草后两轮校稿（2026-09-07「DeepSeek 生文，Kimi 两轮校稿修正」）：
# 片段含 {body} 占位（待校正文）；运行时经 get_effective_prompt("polish_round1/2")
# 三级覆盖取词，此处常量为代码内置默认（提示词库「系统内置」展示用）
POLISH_ROUND1_PROMPT = _skills.fragment("polish", "round1")
POLISH_ROUND2_PROMPT = _skills.fragment("polish", "round2")

# 审核 SOP（fact_check 自动审核的核查规则全文）：本期只注册进提示词库——
# STAGE_CATALOG/default_prompt/三级覆盖链路已可用（admin 设置页改库文本即生效，
# 零发版），但 fact_check 运行时消费方尚未实现，除本常量外暂无代码读取此 stage。
AUDIT_SOP_PROMPT = _skills.skill_body("audit-sop")

# 分页排版轮换指令：同一套风格词下，6 页的构图/布局必须错开（防模板化重复）
_PAGE_LAYOUTS = _skills.fragment_list("image-prompt", "layouts_cn")

IMAGE_PROMPTS = {
    mode: _skills.mode_fragment("image-prompt", mode)
    for mode in ("general", "single", "compare")
}

# 旧版提示词（保留兼容：get_prompt 仍可读 draft_v1 / page_split_v1 / evidence_v1）
PROMPT_VERSIONS = {
    "draft_v1": DRAFT_PROMPTS["general"],
    "page_split_v1": "对文章进行精简和拆分，总文字严格控制到350字以内，包括封面和每一页的文字内容，适合放在图上，每个部分一段话。",
    "evidence_v1": "提取这段话中可验证的事实点（数值、单位、年份、定义、引用、因果），每个事实点标注风险等级。",
}


def get_prompt(name: str, version: str = None) -> str:
    if version:
        key = f"{name}_{version}"
        if key in PROMPT_VERSIONS:
            return PROMPT_VERSIONS[key]
    return PROMPT_VERSIONS[f"{name}_v1"]


def get_draft_prompt(mode: str) -> str:
    return DRAFT_PROMPTS.get(mode, DRAFT_PROMPTS["general"])


# 生图铁律（2026-09-18）：所有生图提示词最末尾必须附带的
# 图上文字范围约束 + 中文文字硬约束——先限制模型自由发挥的文字数量，
# 再锁住给定文案逐字正确，双重杜绝伪字/错字/变形。
_IMAGE_TEXT_HARD_RULE = (
    "\n\n【图上文字范围铁律（最高优先级，违反即废图）】\n"
    "图上文字仅限上述给定文案（ON-IMAGE TEXT / 本页文案），"
    "必须一字不多、一字不少、逐字复制。\n"
    "严格禁止生成任何给定文案以外的文字：标签、卖点、价格、条目、注释、"
    "补充说明、图标内文字，一律禁止。\n"
    "严禁将文案中的句子摘录为标签、结论条、横幅、角标重复展示——"
    "同一内容全图只出现一次。\n"
    "即使画面留白显得空旷，也绝对禁止用自由文字填充。\n"
    "图中所有汉字必须与简体中文规范字形严格一致，使用标准印刷体。\n"
    "严格禁止近形字混淆，例如：焙≠培、拔≠拨、未≠末、己≠已≠巳、"
    "蓝≠篮、度≠渡、辩≠辨≠辫。\n"
    "【渲染质量硬约束】\n"
    "所有文字必须锐利清晰、笔画分明完整、边缘干净，禁止笔画粘连、"
    "模糊、断笔、缺笔、溢墨、锯齿；达到印刷级渲染质量。\n"
    "小字同样必须清晰可辨——每个字的偏旁部首能清楚分辨，"
    "绝不能糊成一团或粘连成块。\n"
    "\n【中文文字硬约束】\n"
    "所有中文属于 LOCKED TEXT / IMMUTABLE TEXT。\n"
    "严格对应GBK简体中文字库。\n"
    "必须严格逐字符复制用户提供的文字。\n"
    "禁止：\n"
    "- 改写\n"
    "- 同义替换\n"
    "- 自动润色\n"
    "- 增字\n"
    "- 漏字\n"
    "- 错别字\n"
    "- 同音字替换\n"
    "- 繁简体转换\n"
    "- 日文新字体（実・対・変・単・図・芸）\n"
    "- 繁体字、异体字\n"
    "- 生成不存在的汉字\n"
    "- 生成类似汉字的伪字符\n"
    "- 修改标点符号\n"
    "每一个汉字必须具有：\n"
    "- 正确笔画\n"
    "- 正确偏旁部首\n"
    "- 正确左右/上下结构\n"
    "- 正确字符比例\n"
    "- 标准现代简体中文字形\n"
    "【字体】\n"
    "使用标准现代简体中文无衬线字体。\n"
    "Visual reference:\n"
    "Source Han Sans SC / Noto Sans CJK SC / PingFang SC\n"
    "禁止：\n"
    "calligraphy\n"
    "handwriting\n"
    "decorative Chinese typography\n"
    "distorted typography\n"
    "3D text\n"
    "perspective text\n"
    "curved text\n"
    "artistic glyph deformation\n"
    "【排版】\n"
    "所有中文：\n"
    "- 水平排列\n"
    "- 正对镜头\n"
    "- 无透视\n"
    "- 正文字号不小于边框的7%\n"
    "- 同一张图内所有正文字号必须统一，禁止大小混排\n"
    "- 高对比度\n"
    "- 字间距正常\n"
    "- 不与图标重叠\n"
    "- 不与边框重叠\n"
    "- 不被装饰元素遮挡\n"
    "\n【物件文字禁令】\n"
    "画面中不得出现自带文字的物体：包装袋/瓶罐标签、说明书、毛巾Logo、"
    "标牌、药盒文字、屏幕界面文字、路牌店招一律禁止；"
    "装饰元素一律无文字。这些“非主要文字”是乱码与伪汉字的最大来源。"
)



import re as _re

# 底色词表：风格库条目描述里的背景色措辞（分类分配背景约束的锚点）
_BG_WORDS = ("深靛紫", "深炭", "炭灰", "深褐", "深咖", "深墨绿", "墨绿",
             "深藏青", "藏青", "深海军蓝", "深灰蓝", "黛蓝", "黛青", "深棕",
             "深紫", "深青", "深灰", "炭黑", "深豆绿", "深藕紫", "深暖灰",
             "深米灰", "墨黑", "深灰黑", "深靛", "砖红", "深酒红")


def _extract_bg_clause(text: str) -> str:
    """从风格描述提取背景色句（第一个含底色词且语义指向背景/底的分句）。"""
    for seg in _re.split(r"[，；。]", text or ""):
        seg = seg.strip()
        if not seg:
            continue
        if any(w in seg for w in _BG_WORDS) and ("背景" in seg or "底" in seg
                                                 or "色" in seg):
            return seg
    return ""


def _bg_lock_clause(mode: str, *texts: str) -> str:
    """图生图（compare/single）背景保持铁律：从风格描述提取底色句，
    要求背景保持该色、忽略参考图环境色——按风格库条目分类分配背景约束。"""
    if mode not in ("compare", "single"):
        return ""
    for t in texts:
        bg = _extract_bg_clause(t)
        if bg:
            return (f"\n\n【背景保持铁律】画面背景必须保持：{bg}。"
                    "参考图仅用于产品/主体外观参考，严格忽略参考图的环境色、"
                    "场景色与背景色，背景绝不出现米白/浅黄/白色。")
    return ""


def get_image_prompt(mode: str, page_body: str, page_index: int = None,
                     template: str = None, style_block: str = None,
                     page_subject: str = None, visual: str = None,
                     style_en: str = None) -> str:
    """组装单页生图提示词。两种模式：

    - 英文视觉模式（visual + style_en 由 visual_writer 扩写产出，2026-09-01）：
      VISUAL DIRECTION（英文场景描述）→ 统一风格英文版 → 本页中文文案逐字渲染
      → 英文约束底座 → 英文布局轮换。gpt-image 对英文视觉指令理解更准，
      中文文案必须原样给出（图上写简体中文）。
    - 中文回退模式（扩写失败）：题材前缀 → 本页文案 → 风格段（含6页统一条款）
      → 中文硬约束底座 → 布局轮换，page_subject 有值时替换通用锚定句。
    """
    if visual:
        parts = [
            IMAGE_PROMPTS_EN.get(mode, IMAGE_PROMPTS_EN["general"]),
            f"\nVISUAL DIRECTION for this page (follow closely):\n{visual}",
        ]
        if style_en:
            parts.append("\nUNIFIED STYLE for ALL 6 pages of this set "
                         "(lighting/palette/typography/decor must stay "
                         f"identical across pages, only layout varies):\n{style_en}")
        parts.append(
            "\nON-IMAGE TEXT (simplified Chinese, render VERBATIM on the card, "
            "see HANZI RULE below):\n" + (page_body or ""))
        parts.append("\nCONSTRAINTS:\n" + _IMAGE_CONSTRAINTS_EN)
        if page_index:
            parts.append("\n" + _PAGE_LAYOUTS_EN[(page_index - 1)
                                                % len(_PAGE_LAYOUTS_EN)])
        parts.append(_IMAGE_TEXT_HARD_RULE)
        return "\n".join(parts)
    # 中文回退模式
    template = template or IMAGE_PROMPTS.get(mode, IMAGE_PROMPTS["general"])
    prompt = template.replace("{page_body}", page_body)
    if style_block:
        prompt += style_block
    prompt += _SHARED_IMAGE_STYLE
    if page_index:
        # 追加本页专属排版指令，让 6 页构图错开（风格段不变，只变布局）
        prompt += _PAGE_LAYOUTS[(page_index - 1) % len(_PAGE_LAYOUTS)]
    prompt += _IMAGE_TEXT_HARD_RULE
    prompt += _bg_lock_clause(mode, prompt)
    return _apply_page_subject(prompt, page_subject)


# 分页文案：由 LLM 把整篇正文改写成 6 页图上文案（替代旧的机械切割，2026-08-20）
# 字数契约单点在 skills/page-split/contract.txt（skill_loader.PAGE_* 常量）
PAGES_PROMPT = _skills.skill_body("page-split")


def get_pages_prompt(body: str) -> str:
    return PAGES_PROMPT.replace("{body}", body)


# 定点重生成：单页文案重写（驳回标记驱动，2026-08-21）
PAGE_REGEN_PROMPT = _skills.skill_body("page-regen")


# ============ 提示词库（系统默认 + 用户自定义，2026-08-20） ============
# 系统默认提示词就是 skills/ 包内容（经上方常量读出）；用户自定义提示词存
# prompt_templates 表。流水线解析顺序：任务创建者在该 (stage, mode) 有「启用」
# 的自定义提示词 → 用之，否则回退系统默认。

STAGE_CATALOG = [
    {"stage": "draft_gen", "label": "正文生成",
     "modes": ["general", "single", "compare"],
     "hint": "任务 query 会追加在提示词之后。"},
    {"stage": "page_split", "label": "分页文案",
     "modes": [None],
     "hint": "用 {body} 引用整篇正文；要求模型输出 6 个字符串的 JSON 数组。"},
    {"stage": "image_gen", "label": "配图生成",
     "modes": ["general", "single", "compare"],
     "hint": "用 {page_body} 引用本页文案；分页排版指令由系统自动追加。"},
    {"stage": "page_regen", "label": "单页重写（定点重生成）",
     "modes": [None],
     "hint": "驳回标记驱动。用 {body} 引用正文、{old_copy} 引用原文案、"
             "{feedback} 引用审核意见、{page_index} 引用页码。"},
    {"stage": "polish_round1", "label": "正文校稿·第1轮",
     "modes": [None],
     "hint": "text_check 起草后自动执行（Kimi 主校）。用 {body} 引用待校正文；"
             "只输出修改后的全文。"},
    {"stage": "polish_round2", "label": "正文校稿·第2轮终校",
     "modes": [None],
     "hint": "第1轮校后文本的终校（Kimi 主校）。用 {body} 引用待校正文；"
             "只输出修改后的全文。"},
    {"stage": "audit_sop", "label": "审核 SOP",
     "modes": [None],
     "hint": "自动审核（fact_check）的核查规则全文；本期仅注册提示词库，"
             "fact_check 运行时未实现，改库文本待运行时上线后生效。"},
]


def default_prompt(stage: str, mode: str = None) -> str:
    """系统默认提示词（提示词库里展示的「系统内置」内容）。"""
    if stage == "draft_gen":
        return DRAFT_PROMPTS.get(mode, DRAFT_PROMPTS["general"])
    if stage == "page_split":
        return PAGES_PROMPT
    if stage == "image_gen":
        return IMAGE_PROMPTS.get(mode, IMAGE_PROMPTS["general"])
    if stage == "page_regen":
        return PAGE_REGEN_PROMPT
    if stage == "polish_round1":
        return POLISH_ROUND1_PROMPT
    if stage == "polish_round2":
        return POLISH_ROUND2_PROMPT
    if stage == "audit_sop":
        return AUDIT_SOP_PROMPT
    raise KeyError(f"unknown prompt stage: {stage}")


async def system_prompt(stage: str, mode: str = None) -> tuple:
    """当前生效的系统默认提示词：admin 落库的覆盖（owner_id IS NULL）优先，
    否则代码内置默认。返回 (content, customized)。"""
    from sqlalchemy import text as _text
    from src.db.session import SessionLocal
    async with SessionLocal() as session:
        r = await session.execute(_text(
            "SELECT content FROM prompt_templates "
            "WHERE stage = :s AND mode IS NOT DISTINCT FROM :m AND owner_id IS NULL "
            "ORDER BY updated_at DESC LIMIT 1"),
            {"s": stage, "m": mode})
        override = r.scalar()
    if override is not None:
        return override, True
    return default_prompt(stage, mode), False


async def get_effective_prompt(stage: str, mode: str, owner_id) -> str:
    """流水线取词入口：创建者启用的自定义 → admin 的系统覆盖 → 代码内置默认。"""
    if owner_id is not None:
        from sqlalchemy import text as _text
        from src.db.session import SessionLocal
        async with SessionLocal() as session:
            r = await session.execute(_text(
                "SELECT content FROM prompt_templates "
                "WHERE stage = :s AND mode IS NOT DISTINCT FROM :m "
                "AND owner_id = :o AND is_active "
                "ORDER BY updated_at DESC LIMIT 1"),
                {"s": stage, "m": mode, "o": str(owner_id)})
            custom = r.scalar()
            if custom:
                return custom
    content, _ = await system_prompt(stage, mode)
    return content
