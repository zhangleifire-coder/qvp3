"""配置：全部走环境变量（与 qvp_mcp 风格一致，pydantic-settings）。"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_COMPONENT_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- 模型密钥（主 DeepSeek / 备1 Kimi 开放平台 / 备2 Kimi Code 会员）---
    deepseek_api_key: str = ""
    kimi_api_key: str = ""
    kimi_code_api_key: str = ""                        # 备2（老 sk-kimi- key），空=禁用第三路由

    # --- 主路由（DeepSeek 官方，OpenAI 兼容）---
    primary_provider: str = "deepseek-official"
    primary_model: str = "deepseek-flash"   # 2026-09-16 起使用官方 V4.1 Flash ID；可用环境变量 PRIMARY_MODEL 覆盖
    deepseek_base_url: str = "https://api.deepseek.com/v1"

    # --- 备1 路由（Kimi 开放平台，OpenAI 兼容；2026-09-10 从 Kimi Code 会员额度
    # 切换到开放平台按量付费，解决周配额上限问题）---
    fallback_enabled: bool = True
    fallback_provider: str = "kimi"
    fallback_model: str = "kimi-k2.6"
    fallback_api: str = "openai-completions"      # 开放平台 OpenAI 兼容协议
    kimi_base_url: str = "https://api.moonshot.cn/v1"

    # --- 备2 路由（Kimi Code 会员，Anthropic 兼容；额度兜底线）---
    fallback2_enabled: bool = True
    fallback2_provider: str = "kimi-code"
    fallback2_model: str = "k3"
    fallback2_api: str = "anthropic-messages"
    kimi_code_base_url: str = "https://api.kimi.com/coding"

    # --- dsh 运行时 ---
    dsh_home: str = str(_COMPONENT_DIR / "dsh-home")   # 会话落盘根目录，docker 挂卷此路径
    dsh_workspace: str = str(_COMPONENT_DIR)           # agent cwd（MCP 工作目录基准）
    dsh_max_tokens: int = 32768                        # 单轮输出上限。注意 deepseek 推理模型
    # 的 max_tokens 含 reasoning_tokens：8192（nanobot 旧值）在 reasoningEffort=high +
    # 长 prompt 下会被推理吃光（实证：turn/end=max-tokens 且正文 0 字符）
    dsh_reasoning_effort: str = ""                     # 空 = 模型默认
    request_timeout_seconds: float = 3000.0            # 对齐 NANOBOT_REQUEST_TIMEOUT_SECONDS 服务器值
    dsh_initialize_timeout_seconds: float = 120.0      # dsh 子进程首启（含 MCP server 冷启动）握手上限

    # --- 服务 ---
    dsh_serve_host: str = "127.0.0.1"
    dsh_serve_port: int = 8901
    dsh_max_concurrent: int = 4                        # 对齐后端 MAX_CONCURRENCY

    # --- MCP（qvp_mcp stdio 子进程）---
    mcp_enabled: bool = True
    mcp_server_name: str = "qvp"
    mcp_command: str = ""                              # 例：C:/.../code/.venv/Scripts/python.exe
    mcp_args: str = '["-m", "qvp_mcp"]'                # JSON 数组
    mcp_pythonpath: str = ""                           # 例：code/ 根目录
    mcp_tool_timeout_ms: int = 1800000                 # 30 分钟，对齐 nanobot toolTimeout
    mcp_extra_env: str = "{}"                          # JSON 对象，如成本回调变量

    # --- 内置工具冲突 ---
    # dsh base 自带全套 coding 工具（shell/fs/web/todo/subagent/workflow…），
    # 与 qvp_mcp 的 mcp__qvp__* 形成旁路：绕开配额/记账/契约（对比分析差距
    # 2、3 实证：bash 数字数、write 写 workspace、read_image 替代 OCR）。
    # 默认全部禁用，模型只剩 mcp__qvp__* + skill。
    disable_builtin_tools: bool = True


def get_settings() -> Settings:
    return Settings()


def refusal_markers() -> list[str]:
    """拒答检测词表（移植自 src/gateway/litellm_adapter.py，保持语义独立）。"""
    return ["我无法", "我不能", "抱歉，我无法", "I cannot", "I'm sorry"]
