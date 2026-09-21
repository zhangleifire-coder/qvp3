from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://qvp:qvp@localhost:5432/qvp"
    redis_url: str = "redis://localhost:6379/0"
    # 文本模型（spec §1.1 已确定选型）
    deepseek_api_key: str = "sk-xxx"      # DeepSeek：正文生成 + 生图提示词
    # 主模型：deepseek-flash 是 DeepSeek V4.1 Flash 的官方 API 标识；改这里全局生效
    deepseek_model: str = "deepseek-flash"
    kimi_api_key: str = "sk-yyy"          # Kimi K3（备1：开放平台按量，api.moonshot.cn）
    # 备2：Kimi Code 会员兜底线（api.kimi.com/coding，anthropic 协议）；
    # 默认空 = 第三级自动禁用，填入老 sk-kimi- 前缀 key 即启用
    kimi_code_api_key: str = ""
    # 备用链开关（2026-09-10 超管控制台在线切换；system_settings 持久化）
    text_fallback1_enabled: bool = True   # 备1 Kimi 开放平台 kimi-k2.6
    text_fallback2_enabled: bool = True   # 备2 Kimi Code k3
    # 图片模型（z-image-turbo，阿里百炼，中文渲染优）
    dashscope_api_key: str = "sk-zzz"     # DashScope API key
    dashscope_base_url: str = "https://ws-7349xztoo3gwseol.cn-beijing.maas.aliyuncs.com/api/v1"
    # 图片生成（gpt-image-2.5-flare，OpenAI 兼容 Images API，经转发机；
    # 可选 gpt-image-2.5-sunburst，同 API 同参数）
    openai_image_base_url: str = ""      # 通道1：LinkAI（OpenAI 兼容，含 /v1）
    openai_image_api_key: str = "sk-xxx" # 通道1 key
    # 通道2：Moacode gpt-image-2.5（OpenAI Responses API，SSE 流式，返回 base64）
    moacode_api_key: str = ""            # cr_... ；空则该通道不可用
    moacode_base_url: str = "https://moacode.org/v1"
    # 通道3（主）：FusionAI gpt-image-2.5（Images API 生成+编辑，返回 b64_json，
    # 1K/2K/4K，支持 6 图并发；生图可能数分钟，读超时给足）
    fusionai_api_key: str = ""           # sk-fusion-... ；空则该通道不可用
    fusionai_base_url: str = "https://api.fusionaix.cn/v1"
    # 通道4（备份，2026-09-08）：openox gpt-image-2.5（OpenAI 兼容 Images API，
    # 只确认支持文生图 /images/generations，不参与图生图）
    openox_api_key: str = ""             # sk-... ；空则该通道不可用
    openox_base_url: str = "https://api.openox.net/v1"
    image_gen_channels: str = "fusion,linkai,moacode,openox"  # fusion 主通道轮询优先
    image_model: str = "gpt-image-2.5-flare"
    image_size: str = "1152x1536"        # 竖版（1152x1536，3:4）
    # 生图画质档（2026-09-01）：gpt-image-2.5 API 默认 auto≠high，网页端等效 high——
    # 不显式传 high 会跑 medium/low，细节纹理锐度明显下降
    image_quality: str = "high"
    # ── 2026-09-01 吸收 8002 优化（全部可独立关闭，默认不破坏现行为）──
    # 正文创作后自动校稿润色一轮（draft_gen 节点内二段式；关=保持旧行为）
    draft_polish_enabled: bool = True
    # 素材库复用：搜图前先按关键词匹配历史 official 实图，命中免搜索免下载
    asset_library_reuse: bool = True
    # 通用模式(general)也搜集/使用实景参考图（默认关=保持纯文生图；
    # 开启后 general 任务将多一道实景审图关卡，属产品方向变化，需用户拍板）
    ref_for_general_enabled: bool = False
    # 视觉主体审核（2026-09-01 图文一致性）：生成后 VL 看图与该页文案比对，
    # 主体不符自动重画一次，仍不符标记 subject_mismatch 交人工把关
    visual_subject_check_enabled: bool = True
    visual_check_model: str = "qwen-vl-max"   # dashscope VL（复用 ocr 的 key/网关）
    # 100% OCR 标准的 VL 申诉通道（2026-09-14 P2）：OCR 判不合格的页先经
    # qwen-vl-max 复核「图中文字是否与文案逐字一致」——一致即放行（OCR 误判申诉
    # 成功），不一致才重生。放行口径仍是 100%，只给 OCR 误判一个复核出口。
    visual_text_appeal_enabled: bool = True
    # 起草文案时是否注入 ref_seed 实景参考图（2026-09-18 默认关闭：文案不看图
    # 起草，避免"不得编写参考图里没有的内容"限制发挥；设 true 可回滚旧行为）
    text_check_use_refs: bool = False
    mock_image_gen: bool = False         # 开发阶段模拟生图（不调 API、不花钱）
    # 搜实景图 provider（openserp 免费默认 / doubao_ark / bing_api 预留）
    # 混合生图（2026-09-21 并入主链路）：程序渲染文字版式 + 每页一次
    # AI 无文字画面（消灭模型伪汉字；成本与整图直出持平）；false 回退
    # 模型直出图文
    image_compose_mode: bool = True
    # 单候选生图（2026-09-21 用户决策）：文字铁律+VL 质检已保障质量，
    # 双候选 OCR 选优的收益不抵成本；true=恒单候选（可回滚）
    image_single_candidate: bool = True
    image_search_provider: str = "openserp"
    openserp_base_url: str = "http://127.0.0.1:7001"
    # 旧 ChatGPT 生图字段（保留占位）
    chatgpt_api_key: str = "sk-zzz"
    chatgpt_proxy_url: str = ""
    # 搜索（证据包网页搜索：doubao 结构化 / deepseek 联网 / kimi 联网，搜实景图：openserp）
    web_search_provider: str = "doubao"   # doubao（结构化来源）/ deepseek（联网总结）/ kimi（联网搜索）
    doubao_search_key: str = "sk-www"     # 豆包搜索 API：证据包
    doubao_ark_key: str = "sk-vvv"        # 豆包方舟：搜实景图（预留）
    kimi_search_cost_per_call: float = 0.03   # Kimi 联网搜索每次调用额外费用（元/次）
    # 队列 / 自适应并发（降并发：测试账户限流，串行处理）
    initial_concurrency: int = 1
    min_concurrency: int = 1
    max_concurrency: int = 1
    # 配图间隔（秒）：批与批之间留处理时间，避免触发限流
    image_gen_delay_seconds: float = 5.0
    # 任务内生图并行批量（2026-08-24）：6 张按批并发调用生图 API，
    # 1=退回串行（openox 老线路防限流用），2-3=linkai 等容忍并发的线路
    image_gen_parallel: int = 2
    # 生图全局兜底价（元/张）：权威费率在 model_rates 表 gpt-image-2.5-flare@<channel>
    # 行（025，fusion=0.2 账单实证待校准），本值仅在表内无对应行时兜底
    image_cost_per_image_cny: float = 0.4
    # OCR（阿里百炼 qwen 系列，模型可按需换 qwen3.5-ocr / qwen3-vl-flash 等）
    ocr_model: str = "qwen-vl-ocr"
    ocr_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    # 按次计费的外部服务（价格不确定，可手工补充，单位：元/次）
    doubao_search_cost_per_call: float = 0.0   # 豆包搜索（证据包）
    openserp_cost_per_call: float = 0.0        # OpenSERP 搜实景图
    # 工作周期：工作 N 小时 → 检修停机 M 小时 → 循环
    work_hours: float = 23.0
    maintenance_hours: float = 1.0
    # 系统配置
    environment: str = "dev"
    log_level: str = "INFO"
    heartbeat_timeout_seconds: int = 30
    auto_suspend_timeout_seconds: int = 5400
    sampling_rate: float = 0.20
    anomaly_min_seconds: int = 5
    anomaly_max_seconds: int = 3600
    # ── 创作 Agent 路径（2026-08-22 改造；网关 2026-09-18 起为 dsh_serve）──
    # 双路径总开关：true=创作段(evidence/正文/分页/生图/OCR)整体交给创作 Agent；
    # false=原 13 节点直连路径（网关故障时秒级回退，软件工程层兜底）
    agent_pipeline_enabled: bool = False
    # Agent 路径变体（2026-09-09）：monolith（默认，agent_production 大节点，
    # 发版安全默认）/ staged（创作段拆为 agent_evidence/draft/pages/assets
    # 4 个独立 Agent 节点，阶段失败只重跑该阶段、成本按节点拆分）。
    # 仅 .env 显式设 AGENT_PIPELINE_VARIANT=staged 才走新路径
    agent_pipeline_variant: str = "monolith"
    # ── 创作网关：dsh_serve 薄层（内嵌 dsh harness，OpenAI 兼容 :8901）──
    dsh_serve_base_url: str = ""         # OpenAI 兼容地址（含 /v1），空=自动回退
    dsh_serve_api_key: str = ""          # 仅 bind 非 localhost 时需要（Bearer）
    dsh_serve_model: str = ""            # 留空 = 用 dsh 路由默认模型预设
    dsh_serve_request_timeout_seconds: float = 0  # 0=用默认 3000s
    # compare 模式（搜图+图生图）在慢网需 ~30-40 分钟，读超时给足
    # MCP 工具进程 → 后端的成本回调
    mcp_callback_base_url: str = "http://127.0.0.1:8003"
    internal_callback_token: str = "qvp-internal-dev"
    # MCP 工具配额（按 task_id 计，防 Agent 失控烧钱的硬限制）
    mcp_max_images_per_task: int = 8     # MCP image 工具单次额度（9-14 起真正的硬顶是 image_total）
    # 任务级出图总预算（2026-09-14 P1 止血，WS4 实测 46 张/任务 ¥9.2 的教训）：
    # MCP 首轮 + garble/主体/AI 审核重生全部路径共用的累计硬顶，到顶停生成进人工。
    # 累计语义——节点重跑/中断续跑不重置（reset 只清非累计 kind）。
    image_budget_per_task: int = 14      # 6 张交付 + 8 张重生余量
    mcp_max_web_searches_per_task: int = 3
    mcp_max_image_searches_per_task: int = 3
    mcp_max_ocr_per_task: int = 8
    # qvp_mcp v2 能力工具（2026-09-03 功能项独立化）：每个 LLM 类/校验类工具
    # 在每任务下的调用上限（按工具各自计，防 Agent 循环烧钱）
    mcp_max_llm_tools_per_task: int = 5    # draft_write/page_split/page_regen 等
    mcp_max_check_tools_per_task: int = 10  # rule_check/cross_check 等确定性校验
    # ── 审核角色权限口径 ─────────────────────────────────────────────
    # true（试运行默认）：任何账号在审核台可切换 A/B/C 任一角色审核（一人担全部工作）；
    # false（正式生产）：恢复按账号角色（users.role）锁定各自单一队列
    role_all_access: bool = True


settings = Settings()
