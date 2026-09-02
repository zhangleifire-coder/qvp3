"""提示词版本库：按 (用途, mode) 提供正文/生图提示词。"""

# 生图硬约束底座（风格无关）：色调/字体/质感由「本篇视觉风格」段落控制
#（2026-08-31 重构：风格必须按 query 题材自适应随机选向、六页统一——
# 把奶油米/深棕字/写实质感等具体风格特征从底座移入风格库各条目描述词，
# 底座只保留排版结构、文字硬约束、装饰原则与主体锚定等跨风格铁律，
# 详见 src/services/style_select.py 与 docs/风格库训练方法总结.md 第八节）
_SHARED_IMAGE_STYLE = (
    "竖版3:4图文卡片。整体观感：自然、真实、舒适、克制，像杂志内页而非模板拼贴。"
    # 底色与边框：不得纯白/纯黑（2026-08-31 用户反馈）
    "底色边框：整卡背景与四周边框不得使用纯白或纯黑，必须用带色相的颜色"
    "（按本篇风格的主色调，如奶油米/米黄/浅卡其/炭灰等），边缘干净利落。"
    # 版式：图文二分 + 呼吸感（分割方式与配色跟随本篇风格）
    "版式：文字区与图片区二分结构（上文下图或上图下文），过渡方式按本篇风格执行，"
    "忌生硬矩形拼贴；留白充足（主色背景至少占画面三成），视觉有呼吸感。"
    # 文字：跨风格硬约束（字体族与颜色跟随本篇风格，但正确性要求全风格一致）
    "文字：大号标题（约为正文字号的2.5倍），层级分明，标题字体与颜色严格按本篇风格执行，"
    "全篇字体系统一致；每行是短句，要点间用细线或小色块分隔。"
    # 2026-09-01 吸收 8002 人工样例规律；同日按用户要求改「模型自选协调强调色」
    "主标题用双色排版：主体字深色，其中1-2个关键词用一个与整体配色和主题协调的"
    "强调色（视觉描述段给出配色时优先用它；未给出时自行选雅致点缀色，"
    "如暖橘/砖红/墨绿/雾蓝/紫檀/芥末金等，不固定某一种）；"
    "对比类标题的「VS」等对比词也用强调色。"
    "文字默认深灰或黑色，直接排在画面的浅色留白区域上，不要底色框、不要深色衬底；"
    "主题色彩色胶囊/圆角标签压白字做重点标注是推荐形式、不算深色框；"
    "全套6页中深色（近黑/深灰）底文字框最多出现1次，严禁每页深色框压白字。"
    "所有文字必须清晰可读、标准中文字体，禁止艺术化变形、阴影、描边、透视扭曲，"
    "正文统一基线对齐、可印刷级清晰；每页图上文字（含标题）30-100 字，"
    "且一篇内各页文字量基本均衡（任意两页相差不超过 25 字），"
    "每页只突出一个核心信息点，不出现字号过小的文字，"
    "图中的每一个汉字都必须是中国大陆规范简体字形，严禁日文新字体（実・対・変・単・図・芸）、"
    "繁体字、异体字、自造字或乱码字符；把图中文字当作需要逐字精确复制的排版内容而非装饰纹理："
    "给定文案一字不差，不增字、不漏字、不改写；拿不准如何正确书写的文字宁可不出现在图中；"
    "不要出现「封面/第X页」等字样。排版不要模板化（每页排版不同），"
    "图片元素不与前页重复。不出现人脸、书籍等元素，尽量不出现带文字的物体。"
    # 装饰：克制原则（具体元素跟随风格）
    "装饰：克制统一，按本篇风格的装饰语言执行，不堆砌、不花哨。"
    "主体清晰不被遮挡、展现完整主体不裁剪关键特征；"
    # 通用画质铁律（2026-09-01 依 API/网页质量差异分析补齐，对齐网页端 Agent
    # 默认画质约束——API 裸调不会自动润色，画质词必须显式写进提示词）
    "主体质感按本篇风格执行，但必须精致干净——画面锐利、细节丰富、纹理细腻、"
    "构图工整，无畸形无伪影无模糊，忌廉价塑料感、忌粗糙未完成的笔触。"
    # 主体锚定（风格库训练方法总结·六-1）：风格词只作光影色调氛围，
    # 画面主体必须是本页文案讲的事物本身，严禁把风格词具象化成隐喻物。
    # get_image_prompt(page_subject=...) 时本句被替换为该页的具体主体句
    "（主体锚定）画面主体必须直接描绘本页文案所讲的事物本身，"
    "风格描述词仅用于光影、色调与氛围，严禁把风格词具象化为植物、发芽、"
    "石缝等隐喻物。"
)

# 通用主体锚定句（_SHARED_IMAGE_STYLE 的锚定子串，_apply_page_subject 替换用）
_SUBJECT_ANCHOR = (
    "（主体锚定）画面主体必须直接描绘本页文案所讲的事物本身，"
    "风格描述词仅用于光影、色调与氛围，严禁把风格词具象化为植物、发芽、"
    "石缝等隐喻物。"
)


# ═══════════════════════════════════════════════════════════════════
# 英文生图骨架（2026-09-01 场景化扩写链路）：
# 上游 visual_writer（nanobot 记忆会话优先/DeepSeek 回退）产出每页英文视觉
# 描述（主体+场景+光位视角）与风格英文版，get_image_prompt(visual=...) 时
# 走本骨架——gpt-image 系列对英文视觉指令理解更准；本页中文文案原样保留
# （图上要写简体中文，必须逐字给出）；校准规则全部翻译保留。
# ═══════════════════════════════════════════════════════════════════
_IMAGE_CONSTRAINTS_EN = (
    "FORMAT: vertical 3:4 Xiaohongshu-style image card. Overall feel: natural, "
    "real, comfortable and restrained — like a polished magazine page, NOT a "
    "templated collage. "
    "BACKGROUND & BORDER: the card background and border must NOT be pure white "
    "or pure black — use tinted colors per the unified style (cream, beige, "
    "light khaki, charcoal etc.), edges clean. "
    "LAYOUT: two-zone composition (text zone + image zone, top/bottom or "
    "bottom/top), divider style per the unified style; never hard rectangular "
    "collage; generous breathing whitespace (background occupies at least 30%). "
    "TYPOGRAPHY: large bold headline ~2.5x body size, clear hierarchy; "
    "typeface family and color strictly follow the unified style and stay "
    "identical across all 6 pages; short phrases per line, thin dividers or "
    "small color ticks between points. "
    # 2026-09-01 吸收 8002 人工样例规律；同日按用户要求改为「模型自选协调强调色」：
    # 不再固定暖橘——强调色由视觉描述层的配色决策给出（与底色/主题协调），
    # 未给出时模型自行选一个与整体色板和谐的点缀色
    "Two-tone headline: headline body in dark ink with 1-2 KEYWORDS in an "
    "ACCENT color that harmonizes with the unified palette and the subject "
    "(use the accent specified in the VISUAL/STYLE sections when given; "
    "otherwise pick a tasteful accent yourself — e.g. warm orange, brick red, "
    "teal, cobalt, plum, mustard, forest green); in comparison cards the 'VS' "
    "or comparison word also takes the accent color. "
    "Text sits dark-on-light directly on pale whitespace (dark gray/black ink, "
    "no backing box, no dark panel behind text); theme-colored CAPSULE or "
    "rounded labels with white text are RECOMMENDED for highlights and do NOT "
    "count as dark boxes; a dark (near-black) text panel may appear on at most "
    "ONE of the 6 pages. "
    "All on-image text must be crisp, "
    "print-quality standard Chinese type — no artistic distortion, no shadows, "
    "no outlines, no perspective warping; 30-100 Chinese characters per page "
    "including headline, kept balanced across the 6 pages (max 25-char "
    "difference between any two pages); one core message per page; no tiny "
    "type. "
    "HANZI RULE (critical): every Chinese character rendered on the card must "
    "be a real, mainland-standard SIMPLIFIED Chinese form — never Japanese "
    "shinjitai variants (実 対 変 単 図 芸), never traditional or variant "
    "forms, never invented pseudo-hanzi or garbled glyphs. Treat the on-image "
    "text as TYPESET COPY to reproduce verbatim — not decorative texture: "
    "no added, missing or rewritten characters; if unsure how to write a "
    "character, omit that word rather than render it wrong. Do NOT draw words "
    "like 「封面」or page numbers. Layout must differ page to page (no template "
    "copying); image elements must not repeat across pages. No human faces, "
    "no books; avoid objects containing printed text. "
    "DECOR: restrained and consistent with the unified style — no sticker "
    "piles, no flashy borders. "
    "QUALITY: sharp, richly detailed, fine textures, tidy composition; no "
    "distortion, no artifacts, no blur; subject unobstructed and complete. "
    "SUBJECT ANCHORING: the depicted subject must be exactly what this page's "
    "Chinese text is about; style words only affect lighting, color and mood — "
    "never replace the subject with symbolic metaphors."
)

_PAGE_LAYOUTS_EN = [
    "PAGE ROLE (cover): hero visual occupies ~2/3 of the card (texture per "
    "unified style), large headline at top, one-line subtitle only, generous "
    "whitespace.",
    "PAGE ROLE (key points): text zone above, image below; the Chinese text "
    "breaks into 2-3 short bullet lines, each optionally led by one consistent "
    "small round icon, thin dividers between points, horizontal line or soft "
    "curve separating text and image zones.",
    "PAGE ROLE (close-up): subject close-up fills the frame (lighting per "
    "unified style), text confined to a bottom quarter band in the style's "
    "primary color.",
    "PAGE ROLE (checklist): rounded-card columns, 2-4 info blocks, one "
    "sub-headline each, clear gaps between cards, card tints within the "
    "style's palette.",
    "PAGE ROLE (scene): full-bleed scene image (texture per unified style), "
    "text placed in a top whitespace zone, image and text joined by a curve "
    "or diagonal.",
    "PAGE ROLE (wrap-up): centered large conclusion text, at most two smaller "
    "lines below, clean visual ending.",
]

IMAGE_PROMPTS_EN = {
    "general": ("General topic/tutorial card, fully AI-generated, no reference "
                "images. Render the Chinese text below verbatim on the card."),
    "single": ("Single-product review card. Use ONLY the reference photos "
               "assigned to this page (they are rotated per page): remove "
               "watermarks and people; do NOT invent photos not given to this "
               "page; the reference photos must differ from those of the other "
               "pages; keep text already on reference photos, add no extra "
               "photos. Render the Chinese text below verbatim on the card."),
    "compare": ("Comparison card. Use ONLY the reference photos assigned to "
                "this page (rotated per page), incorporating BOTH subjects "
                "when available (keep their order): remove watermarks and "
                "people; the reference photos must differ from those of the "
                "other pages. Render the Chinese text below verbatim on the card."),
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
    "general": "请你以小红书博主的写作风格及模式，结合权威可靠信源的数据库，创作一篇图文内容。要求：简洁清晰、结构完整、总分总结构、每段加小标题、400-700字、无绝对化表述、无emoji、中文标点。",
    "single": "请你以小红书博主的写作风格，结合权威可靠信源的数据库，创作一篇单品深度测评图文。围绕单一产品/事物展开，依次讲透：它是什么、原理或关键参数、实测体验、优点、局限、安全/使用提醒、适合谁。要求：简洁清晰、总分总结构、每段加小标题、400-700字、无绝对化表述、无emoji、中文标点，事实数据需有信源支撑。",
    "compare": "请你以小红书博主的写作风格，结合权威可靠信源的数据库，创作一篇对比类图文。客观对比两个主体（产品/学校/方案等），平分笔墨，逐维度列出各自的事实参数、优劣与适用场景，最后给出取舍建议。要求：简洁清晰、总分总结构、每段加小标题、400-700字、无绝对化表述、无emoji、中文标点，事实数据需有信源支撑，不偏袒任何一方。",
}

# 正文人设化共享段（2026-09-01 吸收 8002 对齐人工流程的写作要求）：
# 第一人称真人感人设+自然导语、标题公式（≤25字：主需关键词+核心看点+信息钩子）、
# 细节禁令（禁 emoji/感叹号/波浪号、开头不用「作为」、非必要不用双引号）、
# 免责声明。追加到各模式 DRAFT_PROMPTS 之后，不动各模式的结构要求与字数范围。
_DRAFT_SHARED = (
    "【人设与真人感】以一个合理的第一人称人设写作（亲历者/过来人/真实使用者均可），"
    "开头用自然导语交代背景动机（如「最近不少人在纠结怎么选，我前后对比了好几款」），"
    "避免生硬直接切入知识点；语气务实克制，像正常人在说话，"
    "不用「家人们」「绝绝子」「天花板」等夸张网络用语。"
    "【标题】大标题不超过25字，公式：主需关键词＋核心看点/卖点＋信息钩子"
    "（让读者一眼知道能得到什么），强需求导向。"
    "【细节禁令】标题与内文不用 emoji、感叹号、波浪号；开头不用「作为」；"
    "非必要不用双引号；不用「总而言之」「综上所述」「值得注意的是」等 AI 腔套话。"
    "【合规】涉及价格、功效、健康等内容时克制表述；"
    "有人身安全隐患的操作必须保留安全提醒。"
)

# 校稿润色（2026-09-01 吸收 8002 对齐人工流程两轮校稿：删夸大与未证实信息、
# 去AI腔提真人感）。draft_gen 节点内创作后二段执行，失败/过短沿用原稿；
# 字数口径按 qvp2 现行 700 字上限（8002 同源，未动字数依赖防负优化）。
DRAFT_POLISH_PROMPT = """你是资深内容校稿编辑。请对下面的稿件做一轮校稿润色，只输出校稿后的最终全文，不要输出任何解释。
要求：
1. 事实核查：对没有把握的具体数字、年份、名称，改为约数表述（如「约」「近」）或直接删去，不得保留存疑的精确表述；严禁绝对化、夸大化用词。
2. 真人感：开头导语自然、有亲历感；删掉生硬AI腔（如「总而言之」「综上所述」「值得注意的是」等套话），语气务实克制，像正常人在说话。
3. 标题：大标题不超过25字，保留主需关键词和看点钩子；不用emoji、感叹号、波浪号。
4. 小标题：保持原文的小标题，不新增、不替换；个别明显冗长的可精简至8字以内。
5. 字数：全文控制在700字以内，超出则删减次要信息，保留核心干货。
6. 合规：涉及财产、功效成分、健康等内容的，结尾保留或补充免责声明；有人身安全隐患的操作保留安全警告。统一中文标点。

稿件：
{body}"""

# 分页排版轮换指令：同一套风格词下，6 页的构图/布局必须错开，
# 否则 gpt-image 会把每页都画成同一个模板（2026-08-20 用户反馈「每张图重复套用模版」）
_PAGE_LAYOUTS = [
    "本页是封面页：主视觉大图占画面约三分之二（质感按本篇风格），"
    "大标题置顶部，副标题只一行，整体留白充足。",
    "本页是要点页：上文下图布局，正文拆成2-3个短句要点纵向排列，"
    "每条要点前可配一枚统一的小圆图标，要点间用细线或小色块分隔"
    "（线与色块颜色按本篇风格），文字区与图片区以水平细线或弧线过渡。",
    "本页是特写页：主体特写充满画面（光影按本篇风格），"
    "文字只放在底部约四分之一的主色横条区域内。",
    "本页是清单页：圆角卡片式分栏布局，信息分成2-4块排列，每块一个小标题，"
    "块间留明显间距，卡片底色与背景同为本篇风格的主色系。",
    "本页是场景页：全幅场景图铺满画面（质感按本篇风格），"
    "文字置于顶部留白区内，图与文字以弧线或斜线自然衔接。",
    "本页是总结页：居中大字结论，下方最多两行小字，视觉收尾干净利落。",
]

IMAGE_PROMPTS = {
    "general": "通用科普/教程配图，纯 AI 生成、无参考图。" + "本页文案：{page_body}",
    "single": "单品评测配图，将提供的参考实景图融入画面：去水印、去人物；提供的几张参考图已按本页轮播分配，只使用分配给的这几张、不要脑补其他页没给你的图，且六页中每页参考图必须各不相同；不删减参考图上的文字，也不额外添加其他图片。本页文案：{page_body}",
    "compare": "对比类配图，将两个主体的参考实景图融入画面，每页尽量同时呈现两个主体做对比（参考图顺序不能乱）：去水印、去人物；只使用本页分配到的参考图，且六页中每页参考图必须各不相同。本页文案：{page_body}",
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
# 2026-08-31 按用户反馈放宽字数：图上文字太少内容单薄——每页 30-100 字且六页均衡
PAGES_PROMPT = """你是小红书图文编辑。把下面的文章改写成 6 页图上文案，用于竖版图文卡片。
要求：
1. 输出严格的 JSON 数组，恰好 6 个字符串，不要输出任何其他文字、解释或 markdown 代码围栏。
2. 每页图上文字（含小标题与标点）最少 30 字、最多 100 字：
   第 1 页封面 = 主标题 + 一句钩子 + 一行辅助说明；
   第 2-5 页每页一个核心信息点 = 小标题 + 2-3 句干货（讲透这个点）；
   第 6 页结尾 = 一句总结 + 适合谁/行动建议 + 一句补充。
3. 六页文字量必须基本均衡：动笔前先规划好每页约 50-70 字的骨架，
   任意两页字数相差不得超过 25 字；严禁某页只有十几个字而另一页接近 100 字。
4. 忠于原文的事实与数据，不得编造；小标题与表述忠于原文、不自行改写或精简措辞；
   次要细节留在正文，图上只放这一页的核心信息点。
5. 全部纯文本：不用 markdown 符号（#、*、- 等），不用 emoji，无绝对化表述，中文标点。
6. 每页文字都要语句完整通顺、能直接排版在图片上。

文章：
{body}"""


def get_pages_prompt(body: str) -> str:
    return PAGES_PROMPT.replace("{body}", body)


# 定点重生成：单页文案重写（驳回标记驱动，2026-08-21）
PAGE_REGEN_PROMPT = """你是小红书图文编辑。下面是一篇图文的完整正文，以及其中第 {page_index} 页的原图上文案。
该页在人工审核中被驳回，审核意见如下：
{feedback}

请参考正文，重写第 {page_index} 页的图上文案。
要求：
1. 逐条解决审核意见中的问题，不得再出现同类问题。
2. 忠于正文的事实与数据，不得编造；纯文本，不用 markdown 符号和 emoji，中文标点。
3. 重写后整页文字（含小标题与标点）30-100 字，且与该篇其他页的文字量基本均衡
   （相差不超过 25 字），语句完整通顺，能直接排版在图片上。
4. 只输出该页文案本身，不要输出页码、解释或任何其他文字。

正文：
{body}

原第 {page_index} 页文案：
{old_copy}"""


# ============ 提示词库（系统默认 + 用户自定义，2026-08-20） ============
# 系统默认提示词就是本文件里的常量；用户自定义提示词存 prompt_templates 表。
# 流水线解析顺序：任务创建者在该 (stage, mode) 有「启用」的自定义提示词 → 用之，
# 否则回退系统默认。

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
