# dsh_serve —— DeepSeek Harness 创作网关薄层

OpenAI 兼容创作网关（dsh harness 薄层）。协议与原网关 1:1 对齐，后端
`src/gateway/dsh_client.py` 以 `DSH_SERVE_BASE_URL` 指向本服务
（`http://127.0.0.1:8901/v1`）。

底座：DeepSeek Harness（`dsh`），经官方 Python SDK `deepseek-harness-sdk`
驱动常驻 `dsh --profile sdk` 子进程（多轮工具编排、会话持久化、流式事件）。
**dsh 版本钉死：`0.1.2-rc.1`**（pypi 写法 `0.1.2rc1`，spike 2026-09-03 实测
版本；dsh 处于 developer preview，升级前必须回归）。SDK 自带运行时
（`deepseek-harness-runtime-bin` wheel），部署机不需要系统 Node.js。

验证记录见 `docs/dsh-spike-验证记录.md`。

## 接口

- `POST /v1/chat/completions`（兼容挂 `/chat/completions` 裸路径）
  - body：`{messages:[{role:"user",content}], session_id, stream:true, model?}`
  - SSE：`delta.content` 正文、`delta.reasoning_content` 推理、末尾 usage
    chunk（`prompt_tokens/completion_tokens/total_tokens`）+ `finish_reason` + `[DONE]`
  - `stream:false` 返回标准 chat.completion JSON
  - `session_id`：调用方指定；同 session 多轮记得历史（纠错重问依赖）；
    会话落盘 `$DSH_HOME/sessions/`
  - **进程重启恢复**：dsh sdk profile 不支持跨进程冷恢复（重启后同
    session_id 再建会话会被持久化层以 "id collision" 拒绝，spike 实测）。
    薄层自做：每轮成功后在 `$DSH_HOME/dsh-serve/transcripts/` 记录
    user/assistant 正文；撞 collision 时自动 fork 新别名 `<sid>--r<N>`，
    把历史作为前言拼进 prompt 重发（碰撞在 turn 开始即失败，不耗 token），
    调用方无感知
  - 错误：首字节前失败 → HTTP 502（子进程崩溃/主备均失败）/ 504（超时）；
    流式中途失败 → SSE `{"error": ...}` chunk + `[DONE]`
- `GET /health`：存活探测，含 dsh 子进程状态（主/备路由 started/alive/
  last_start_error）、MCP 状态、并发占用

## 启动

```bash
pip install -r dsh_serve/requirements.txt   # 或合并进主 requirements.txt 后统一装
# 必填：DEEPSEEK_API_KEY；备路由：KIMI_API_KEY
python -m dsh_serve
```

## 配置（全部环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | （必填） | 主路由 DeepSeek key |
| `KIMI_API_KEY` | 空 | 备1 Kimi 开放平台 key；为空则禁用该级 |
| `KIMI_CODE_API_KEY` | 空 | 备2 Kimi Code 会员 key（老 sk-kimi- 前缀那把）；**默认空 = 第三级自动禁用** |
| `PRIMARY_MODEL` | `deepseek-v4-flash` | 主模型（2026-09-10 起 flash，价约 pro 1/3） |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com/v1` | 主路由端点 |
| `FALLBACK_MODEL` / `FALLBACK_API` | `kimi-k3` / `openai-completions` | 备1 模型与协议（开放平台） |
| `KIMI_BASE_URL` | `https://api.moonshot.cn/v1` | 备1 端点 |
| `FALLBACK2_MODEL` / `FALLBACK2_API` | `k3` / `anthropic-messages` | 备2 模型与协议（Kimi Code 会员） |
| `KIMI_CODE_BASE_URL` | `https://api.kimi.com/coding` | 备2 端点 |
| `DSH_HOME` | `dsh_serve/dsh-home` | dsh 数据根（会话落盘），**docker 挂卷此路径** |
| `DSH_WORKSPACE` | `dsh_serve/` | agent cwd / MCP 子进程 cwd |
| `DSH_SERVE_HOST` / `DSH_SERVE_PORT` | `127.0.0.1` / `8901` | 监听地址 |
| `DSH_MAX_CONCURRENT` | `4` | 全局并发上限（asyncio.Semaphore，对齐后端 MAX_CONCURRENCY） |
| `REQUEST_TIMEOUT_SECONDS` | `3000` | 单轮请求超时（对齐 DSH_SERVE_REQUEST_TIMEOUT_SECONDS 值） |
| `DSH_INITIALIZE_TIMEOUT_SECONDS` | `120` | dsh 子进程首启握手上限（含 MCP server 冷启动；qvp_mcp 在云盘目录 import 约需 52s，务必大于该值） |
| `DSH_MAX_TOKENS` | `32768` | 单轮输出上限。**注意 deepseek 推理模型的 max_tokens 含 reasoning_tokens**：旧值 8192 在 reasoningEffort=high + 长 prompt 下会被推理吃光导致空响应（2026-09-05 联调实证，见验证记录补测 3） |
| `DSH_REASONING_EFFORT` | 空（模型默认） | 推理强度（off/low/high/max） |
| `FALLBACK_ENABLED` | `true` | 备1 降级开关 |
| `FALLBACK2_ENABLED` | `true` | 备2 降级开关（另需 `KIMI_CODE_API_KEY` 非空才进路由计划） |
| `MCP_ENABLED` | `true` | 挂 qvp_mcp 总开关 |
| `MCP_COMMAND` | 空（不挂） | qvp_mcp 解释器路径，如 `code/.venv/Scripts/python.exe` |
| `MCP_ARGS` | `["-m", "qvp_mcp"]` | JSON 数组 |
| `MCP_PYTHONPATH` | 空 | 须指向 `code/` 根（qvp_mcp 依赖 src/） |
| `MCP_TOOL_TIMEOUT_MS` | `1800000` | 单次工具调用超时（30 分钟） |
| `MCP_EXTRA_ENV` | `{}` | JSON 对象，注入 MCP 子进程（如成本回调变量；建议含 `LITELLM_LOCAL_MODEL_COST_MAP:true`，缓解 qvp_mcp 冷启动 import 慢） |
| `DISABLE_BUILTIN_TOOLS` | `true` | 禁用 dsh base 全套内置 agent 工具（shell/fs 读写检索/read_image/编辑器/todo/goal/subagent/workflow/web 检索及 goal/plan 行为插件），模型只剩 `mcp__qvp__*` + skill（对比分析差距 2、3 修复） |

## 内置工具禁用（2026-09-07 扩展）

serve patch 默认对 `sdk_runner.DISABLED_BUILTIN_ROWS` 全部行置
`disabled: true`（id 对照 sdk profile dump 逐一确认）：tool-bash/tool-pwsh、
tool-jobs、tool-fs（read/write/edit/read_image）、tool-fs-search（glob/grep）、
tool-str-replace-editor、tool-todo、tool-goal、tool-ralph、tool-subagent*、
tool-workflow、tool-web、plan-mode（含 preset 层的嵌套 `planning:plan-mode`
——exit_plan_mode 的残留来源）。动机：内置 shell/write/read_image 曾打开
旁路——绕开 MCP 配额与 tool_ledger 记账、写 workspace 无效文件、approval
升级空跑（对比分析第二节）。保留 skill 三件套与 MCP 客户端。实测生效：
request/header 地面真值工具目录 = 4×`mcp__qvp__*` + `skill`。
代价：Agent 失去"bash 精确数字数"的合规辅助，pages 合规率可能略降，灰度期关注。

⚠️ 禁用地雷：`goal-round-driver` 是 turn 驱动器，禁用它会导致请求挂死
（2026-09-07 实证），永远不要在禁用列表里加行为插件（goal/subagent
provider 等），只禁模型面 tool-* 行。

## 其他口径对齐（2026-09-07）

- **contextWindow**：settings.yaml 对两条路由显式覆盖 131072
  （`llm-deepseek.defaultContextWindow` + kimi 模型条目），修正 dsh 默认 1M
  导致的 compaction/截断阈值失真（对比分析差距 4）。
- **usage 累加**：`StreamAggregate` 按 step 逐条累加（deepseek 每个 step 一条
  usage 事件），发给客户端的 usage chunk 为累计值（dsh_client
  last-non-zero-wins 消费口径兼容）。修复长循环成本低估约 10 倍的问题
  （差距 6）。注意：node_events 成本会因此"变高"，属口径修正而非涨价。
- **model 前缀**：返回的 model 字段带 `dsh:` 前缀（`dsh:deepseek-v4-pro` /
  降级 `dsh:k3`）。后端
  `cost_tracker.estimate_cost` 是子串匹配，`dsh:deepseek-v4-pro` 命中
  deepseek 价、`dsh:k3` 命中 kimi 价——**实测兼容，src/ 无需改动**
  （`tests/test_failover.py::test_cost_tracker_parses_dsh_prefix` 固化）。
- **temperature（差距 5）：放弃**。dsh SDK initialize 仅接受
  provider/model/reasoningEffort/maxTokens（sdk-jsonrpc-server 源码核实），
  wire 层虽支持 temperature（LlmCallConfig），但 serve 链路无注入点；
  改 SDK client.py 是改 site-packages，不可持久。当前跑模型默认（1.0），
  验收口径注明与预设基线的差异。

## 三级 failover（2026-09-10 起）

dsh 原生不做跨路由切换（spike 已证），薄层自做三级：**主 deepseek-v4-flash
（DeepSeek 官方）→ 备1 kimi-k3（Kimi 开放平台，openai-completions）→ 备2
k3（Kimi Code 会员，anthropic-messages，`KIMI_CODE_API_KEY` 空则该级不进
路由计划）**。失败判定：连接错误/子进程崩溃/超时/max-tokens 截断/空响应/
拒答词命中，逐级同 session 整轮重发。流式纪律：reasoning-delta 实时透传，
content/usage chunk 缓冲到当轮质量检查通过后才 flush——任何失败形态都能
干净地整体降级，客户端永不见半截正文（拒答/截断判定都在正文缓冲上做）。
全挂 → 502「All routes failed」。

每个路由一个懒启动 dsh 子进程（三路由共享 `_boot_lock` 首启串行化），
settings.yaml 按需注册 kimi / kimi-code 两个 pi-ai provider（密钥只以
apiKeyEnv 引用，不落盘）。/health 暴露三路由各自状态。

实测（2026-09-10）：主路由指死端口 → 自动降备1 kimi-k3 成功应答
（degraded=True，original_error 留痕）；备2 链路单测覆盖（顺序/无 key 跳过/
三级全挂聚合）。

## 并发模型

- 全局 `asyncio.Semaphore(DSH_MAX_CONCURRENT)` 限并发请求；
- 同一 `session_id` 加 per-session 锁串行（dsh 会话日志追加写）；
- 每个模型路由一个常驻 dsh 子进程，路由内串行、路由间并行；
- SDK 为同步 API，每请求一个 daemon worker 线程，事件经 queue 桥回 SSE。

## 容错语义（对齐 `failOnToolError:false`）

MCP 挂不上不阻断启动：patch 里 `failOnStartupError:false`，工具调用失败由
模型侧感知降级。`/health` 的 `mcp.command_exists` 可事前发现路径配置错误。

## 遗留

- qvp_mcp 本体联测 ✅ 已过（见 `docs/dsh-spike-验证记录.md` 补测记录）。
  注意：qvp_mcp 冷启动 import 在本机 OneDrive 目录下约 52s（litellm 远程
  价表 + 云盘 IO），超过 MCP SDK 60s 握手余量时首挂会落到自动重连；
  已通过 `MCP_EXTRA_ENV={"LITELLM_LOCAL_MODEL_COST_MAP":"true"}` 缓解，
  首次请求后 1–2 分钟内工具就绪。
- Kimi 备路 ✅ 已实测（主路由指死端口 → 自动降级 k3，见补测记录）。
- 两个 dsh 子进程并发首启会竞争 `$DSH_HOME/profiles/node_modules.lock`
  → 组件内已加首启串行锁（`_boot_lock`）；被强杀遗留的
  `profiles/node_modules` 半成品会导致下次启动报 "not a dsh-managed
  module proxy"，删掉该目录（或整个 dsh-home）即可恢复。
- 冷恢复是薄层 transcript 重放（前言拼历史），不是 dsh 原生会话接续：
  恢复后的会话在 dsh 侧是新会话，长历史会被截断到最近 20000 字符；
  纠错重问场景（近几轮）完全够用，跨长任务的多节点编排记忆不保证完整。
- Windows 上 `os.kill SIGTERM` 是强杀（无 lifespan 收尾）；docker/Linux
  部署下 SIGTERM 走优雅退出，本地开发停止请直接关窗口或接受强杀
  （会话与 transcript 均已落盘，强杀不丢历史）。
