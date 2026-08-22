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


settings = Settings()
