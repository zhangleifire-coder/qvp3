from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://qvp:qvp@localhost:5432/qvp"
    redis_url: str = "redis://localhost:6379/0"
    # 文本模型（spec §1.1 已确定选型）
    deepseek_api_key: str = "sk-xxx"      # DeepSeek 4 Pro：正文生成 + 生图提示词
    kimi_api_key: str = "sk-yyy"          # Kimi K3：校稿检查（一轮）
    # 图片模型（z-image-turbo，阿里百炼，中文渲染优）
    dashscope_api_key: str = "sk-zzz"     # DashScope API key
    dashscope_base_url: str = "https://ws-7349xztoo3gwseol.cn-beijing.maas.aliyuncs.com/api/v1"
    # 图片生成（gpt-image-2，OpenAI 兼容 Images API，经转发机）
    openai_image_base_url: str = ""      # OpenAI 兼容地址（含 /v1，如 https://api.openox.net/v1）
    openai_image_api_key: str = "sk-xxx" # OpenAI 图生 key
    image_model: str = "gpt-image-2"
    image_size: str = "1152x1536"        # 竖版（1152x1536，3:4）
    mock_image_gen: bool = False         # 开发阶段模拟生图（不调 API、不花钱）
    # 搜实景图 provider（openserp 免费默认 / doubao_ark / bing_api 预留）
    image_search_provider: str = "openserp"
    openserp_base_url: str = "http://127.0.0.1:7001"
    # 旧 ChatGPT 生图字段（保留占位）
    chatgpt_api_key: str = "sk-zzz"
    chatgpt_proxy_url: str = ""
    # 搜索（证据包网页搜索：doubao 结构化 / deepseek 联网，搜实景图：openserp）
    web_search_provider: str = "doubao"   # doubao（结构化来源）/ deepseek（联网总结）
    doubao_search_key: str = "sk-www"     # 豆包搜索 API：证据包
    doubao_ark_key: str = "sk-vvv"        # 豆包方舟：搜实景图（预留）
    # 队列 / 自适应并发（降并发：测试账户限流，串行处理）
    initial_concurrency: int = 1
    min_concurrency: int = 1
    max_concurrency: int = 1
    # 配图间隔（秒）：每张图之间留足处理时间，避免触发限流
    image_gen_delay_seconds: float = 5.0
    image_cost_per_image_cny: float = 0.2   # 每张生图成本（元，客户确认 2026-08-19）
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
    # ── Nanobot 全链创作 Agent（2026-08-22 改造）──────────────────────
    # 双路径总开关：true=创作段(evidence/正文/分页/生图/OCR)整体交给 Nanobot Agent；
    # false=原 13 节点直连路径（Nanobot 故障时秒级回退，软件工程层兜底）
    agent_pipeline_enabled: bool = False
    nanobot_base_url: str = "http://127.0.0.1:8900/v1"  # OpenAI 兼容地址（含 /v1）
    nanobot_api_key: str = ""            # 仅 bind 非 localhost 时需要（Bearer）
    nanobot_model: str = ""              # 留空 = 用 nanobot 默认模型/主备预设
    nanobot_request_timeout_seconds: float = 1800.0  # 全链一次跑 15-25 分钟，读超时给足
    # MCP 工具进程 → 后端的成本回调
    mcp_callback_base_url: str = "http://127.0.0.1:8000"
    internal_callback_token: str = "qvp-internal-dev"
    # MCP 工具配额（按 task_id 计，防 Agent 失控烧钱的硬限制）
    mcp_max_images_per_task: int = 8     # 6 张交付 + 2 张去重重生余量（¥0.2/张）
    mcp_max_web_searches_per_task: int = 3
    mcp_max_image_searches_per_task: int = 3
    mcp_max_ocr_per_task: int = 8
    # ── 审核角色权限口径 ─────────────────────────────────────────────
    # true（试运行默认）：任何账号在审核台可切换 A/B/C 任一角色审核（一人担全部工作）；
    # false（正式生产）：恢复按账号角色（users.role）锁定各自单一队列
    role_all_access: bool = True


settings = Settings()
