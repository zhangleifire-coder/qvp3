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
