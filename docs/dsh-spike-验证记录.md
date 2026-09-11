# dsh（DeepSeek Harness）替换 Nanobot 创作网关 —— Spike 验证记录

- 日期：2026-09-03
- 验证版本：`@deepseek-ai/dsh` **0.1.2-rc.1**（`npm install -g`，Node v24.13.1 / npm 11.8.0，Windows + Git Bash）
- 实验目录：`../spike-dsh/`（未改动 `code/` 下任何项目代码；本文档是唯一写入项目的产出）
- API 密钥：从《模型服务与密钥交接-20260903.md》第 1 节就地读取注入环境变量（`DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL`），**本文档不含任何密钥值**
- 成本：全程仅 6 次短消息调用（deepseek-v4-pro，max_tokens ≤ 4096）

参考文档：
- 官方仓：https://github.com/deepseek-ai/deepseek-harness （docs/ 下各子系统文档，以下引用均出自 master 分支原文）
- CLI 行为参考：`apps/cli/reference/README.md`
- Python SDK 参考：`python/sdk/README.md`、`docs/user/guide/python-sdk.md`
- 模型配置指南：`docs/user/guide/providers.md`
- 配置目录（生成自源码）：`docs/config-catalog.md`
- 子系统文档：`docs/subsystems/{skills,llm-streaming,session,web-server}.md`

---

## a. 长期驻留 server 形态（OpenAI 兼容 POST /chat/completions + session_id 会话隔离）

**结论：⚠️ 需插件/薄层开发（无原生 OpenAI 兼容端点，但薄 serve 层已实测可行）**

**证据：**

1. 原生形态排查（文档依据）：
   - `dsh web` 启动的是浏览器 GUI 宿主（默认 127.0.0.1:3080），HTTP 层只有内部 Typert RPC：`POST /api/<namespace>/<method>`（docs/api-gateway.md），**不是** OpenAI 兼容端点；`docs/subsystems/web-server.md` 明确 webserver "serves browsers only"。
   - `headless` profile 一次性任务后即退出，CLI 参考原文："The shipped headless profile mounts no browser Connection, HTTP server, Web runtime, or browser client, and opens no listening port."
   - 全仓文档/配置目录检索无任何 OpenAI 兼容 server 端点。
2. 薄 serve 层实测（推荐路径 = **Python SDK + FastAPI**，与现有后端同栈）：
   - `pip install deepseek-harness-sdk`（自带运行时，无需系统 Node），`DeepSeekHarness(dsh_home=..., cwd=..., provider="deepseek-official", model="deepseek-v4-pro")` 惰性启动并**常驻复用**一个 `dsh --profile sdk` 子进程，直到 `close()`。
   - spike 实现 `spike-dsh/serve.py`（约 120 行）：`POST /v1/chat/completions`，body 取 `messages` 末条 user 内容为 prompt、`session_id` 直传 `harness.run(session_id=...)`、`stream:true` 走 SSE。
   - 实测（uvicorn 127.0.0.1:18900）：两轮 curl 同一 `session_id=spike-serve-001` 均正常 SSE 返回（详见 b/d）。会话隔离由 SDK 的 session_id 天然保证（不同 id = 独立持久化会话）。

**对迁移的影响：** 现有 `src/gateway` 的 OpenAI 兼容调用方只需改 base URL 指向该薄层；薄层自行实现 session_id 透传、SSE 字段映射、错误/主备逻辑。工作量估计见文末。

---

## b. SSE 流式（reasoning_content + usage）

**结论：⚠️ 需薄层映射（dsh 内部流式数据齐全：推理 delta 与 usage 都有，实测确认；但无原生 OpenAI SSE 格式，需在 serve 层做字段映射）**

**证据（实测）：**

1. SDK `on_notification` 回调实时收到 `session.event` 通知，其中 `assistant/chunk` 事件流含：
   - `{"chunk": {"type": "block-start", "blockType": "reasoning"}}` → 推理块开始
   - `{"chunk": {"type": "reasoning-delta", "text": "..."}}` → 推理增量（等价 Nanobot 的 `delta.reasoning_content`）
   - `{"chunk": {"type": "text-delta", ...}}` → 正文增量（等价 `delta.content`）
   - `{"chunk": {"type": "usage", "usage": {"inputTokens": 8541, "outputTokens": 147, "totalTokens": 8688, "cacheReadTokens": ..., "reasoningTokens": ...}}}` → 用量
   （原始事件 dump 存于 `spike-dsh/events-dump.txt`；`assistant/message` 汇总事件也带同样 usage。）
2. serve 层映射后实测输出：SSE 共 298 行，其中 **102 条 `delta.reasoning_content`、44 条 `delta.content`、1 条 usage chunk（prompt_tokens/completion_tokens/total_tokens）、finish_reason=stop、[DONE]**（`spike-dsh/sse1.txt`）。
3. 文档依据：`docs/subsystems/llm-streaming.md` —— "Adapters emit usage before the terminal finish"，`TokenUsage` 含 input/output/cache/reasoning 分类计数。

**对迁移的影响：** 前端/后端消费的 SSE 字段语义可 1:1 还原（reasoning_content、content、usage、finish_reason）。注意 usage 是"收尾一条"而非逐 chunk，与 Nanobot 行为一致则无碍。

---

## c. MCP 挂接（stdio MCP server + 30 分钟工具超时）

**结论：✅ 可行（已用 FastMCP stdio dummy server 实测挂通；⚠️ qvp_mcp 本体未实测 —— code/.venv 不存在、项目依赖未安装）**

**证据：**

1. 机制实测：`dsh-mcp-client` 随 CLI 自带（CLI 参考："ships `@deepseek-ai/dsh-mcp-client` as a dependency for patch layers"）。headless 默认不含，用 patch 插入一行即可（`spike-dsh/mcp.patch.yml`）：

   ```yaml
   - insert:
       - id: mcp-dummy
         name: '@deepseek-ai/dsh-mcp-client'
         config:
           transport: stdio
           serverName: qvp
           command: <venv>/Scripts/python.exe
           args: [<spike>/dummy_mcp.py]
           env: {}
           cwd: <spike>
           toolCallTimeoutMs: 1800000   # 30 分钟
           failOnStartupError: true
   ```

2. 实测命令与输出：

   ```
   dsh --profile headless --patch ./mcp.patch.yml \
     "Call the tool mcp__qvp__echo with text 'hello-dsh', then reply with only the tool's return value."
   → stdout: ECHO:hello-dsh   (exit=0)
   ```

   工具以 `mcp__<serverName>__<tool>` 命名进入模型工具目录，与 Nanobot 的命名习惯同形。dummy server 是 FastMCP 4.0.2 stdio（与 qvp_mcp 同框架同传输）。
3. 超时：config-catalog 明确 `toolCallTimeoutMs` 为"Per-tool-call timeout in milliseconds"，**1800000（30 分钟）通过 schema 校验并生效于启动**（failOnStartupError:true 下 boot 成功）；未实际等待 30 分钟验证。
4. 多 server：patch 可插入多条 mcp-client 行，serverName 唯一即可。

**对迁移的影响：** qvp_mcp 启动方式不变（`python -m qvp_mcp`，PYTHONPATH 指向 code/），只需把 command/args/env/cwd 写进 patch。env 支持注入 `MCP_CALLBACK_BASE_URL` / `INTERNAL_CALLBACK_TOKEN` 等。**遗留风险**：qvp_mcp 本体（含 FastMCP 版本、工具签名、长耗时生图调用）未在本机跑通，迁移前需在装好项目依赖的环境补一次实测。

---

## d. 会话复用与记忆（同 session_id 记忆 + 长期会话 + 落盘路径可配置）

**结论：✅ 可行（实测通过）**

**证据（实测）：**

1. SDK 暗号测试（`spike-dsh/sdk_memory_test.py`）：session_id=`spike-mem-001`，TURN1 告知暗号"紫貂-7429"，TURN2 询问 → 回答含暗号，`MEMORY_OK: True`，两轮 finish_reason 均为 `completed`。
2. 经 serve 层 HTTP 复测：同 session_id 第二轮正确回答上一轮算式结果（221）。
3. 跨任务长期会话：SDK 文档原文 "Reusing both a harness and session id continues the durable conversation and session-owned resources"——session_id 由调用方指定（可等于业务 task_id），会话持久化后**进程重启亦可恢复**（session 落盘，cold resume 由 session-controller 支持，见 docs/api-gateway.md "automatically resumes ordinary cold sessions"）。
4. 落盘位置与可配置性：实测生成 `$DSH_HOME/sessions/<workspace-hash>/session-<uuid>/session.jsonl.zstd`（另有投影缓存 `storages/session_projcache/`）。**`DSH_HOME` 环境变量（或 SDK `dsh_home` 参数）整体指定数据根目录**，docker 卷挂载该目录即可；CLI 参考明确 "The SDK deliberately never discovers `~/.dsh`"（路径完全显式）。

**对迁移的影响：** 会话模型与 Nanobot 的 session_id 语义等价，且更强（进程重启后可冷恢复）。compose 里把 dsh_home 挂成卷即可满足持久化要求。

---

## e. 模型主备（主失败自动切备）

**结论：❌ 原生不支持跨 provider/模型 fallback（但有明确替代方案：serve 层自行 failover，改动小）**

**证据（文档+源码依据）：**

1. 全仓文档与 config-catalog 检索无 `fallbackModels` / 跨路由切换配置（对比：Nanobot config.json 有 `fallbackModels`）。
2. 唯一的容错机制是**同路由重试**：`dsh-llm` 的 `RetryPolicyConfig`（源码 `dsh-llm/lib/types/retry-policy.d.ts`）——`normal` 模式"Retry only configured transient failure codes, max 5 retries"，执行点是"the agent's failed-step extension point"，**始终重试同一 provider 路由**，不切换模型。
3. 备用模型可配但需手动/外层切换：`llm-pi-ai` 支持自定义 provider（`api: anthropic-messages | openai-completions | openai-responses` + baseURL + apiKeyEnv），可把 Kimi（`https://api.kimi.com/coding`，模型 `k3`）配成第二 provider；SDK 初始化参数 `provider`/`model` 按次选择。serve 层捕获主路由失败后换 provider 重跑即可（failover 决策权本来就在我们手里，与现有 `src/gateway/failover.py` 同构）。

**对迁移的影响：** 现有 failover.py 的重试/切换逻辑可平移到 serve 层（catch → 换 provider 重发），工作量含在薄层内；代价是失败那一次的首 token 延迟偏高（整轮重发）。Kimi 侧协议需实测确认（coding 端点协议形态），未实测。

---

## f. 并发上限

**结论：⚠️ 无"server 并发请求数"原生配置（因为没有原生 server）；并发度由 serve 层自行控制（信号量/进程池），dsh 内部有相关旋钮**

**证据（文档依据）：**

- dsh 侧已有的并发配置均非"对外请求限流"：`maxParallelToolCalls`（单 agent 单 step 内并行工具调用上限）、`maxConcurrentAgents`（workflow 并发 agent 上限，默认 min(16, cores-2)）、`maxParallelSubCalls` 等（config-catalog）。
- SDK 形态下一个 `DeepSeekHarness` = 一个常驻 dsh 子进程；`session_prompt()` 立即返回、不同 session_id 的 run 可并行推进。serve 层用 `asyncio.Semaphore(N)` 即可实现等价于 Nanobot 的并发上限；需要更强隔离时可起多 harness 进程做进程池。

**对迁移的影响：** 并发控制代码自写（约 10 行信号量），策略自由度反而更高（可按 session/全局两级限流）。

---

## g. skill 机制

**结论：✅ 可行（格式与加载已实测）**

**证据（实测+文档）：**

1. 格式（docs/subsystems/skills.md）：skill = **目录包 `<name>/SKILL.md`（frontmatter 含 kebab-case `name` + `description`）或扁平 `<name>.md`**；frontmatter 支持 `disable-model-invocation` / `user-invocable`。skill 是**按需注入的 Markdown 指令**（模型经 `skill({name})` 工具读取正文），与工具（可执行函数）不同：skill 只提供指导文本和资源引用，不提供可调用能力。
2. 加载（`dsh-skill-filesystem` 本地 provider，按优先级扫描）：

   | 优先级 | 根目录 |
   |---|---|
   | 100 | `<项目根>/.dsh/skills` |
   | 200 | `<项目根>/.agents/skills` |
   | 300 | 自定义 `customSkillDirs` |
   | 400 | `<dshHome>/skills` |
   | 500 | `<agentsHome>/skills` |
   | 600 | 打包内置（默认禁用） |

   有文件监视（Chokidar），增删 skill 热生效。
3. 实测：在 spike 工作区建 `.dsh/skills/spike-greeting/SKILL.md` 后，headless 询问可用 skill 目录 → 模型列出 `spike-greeting`（模型在首轮 `<available_skills>` 系统提示中收到 name+description 目录，按需调用 `skill` 工具取正文）。

目录示例：

```
<workspace>/.dsh/skills/
└── xhs-copywriting/          # 例如"小红书文案风格"skill
    ├── SKILL.md              # frontmatter: name/description + 指令正文
    └── references/           # 可选资源（模板、词表），正文相对引用
        └── tone-guide.md
```

**对迁移的影响：** 现有创作流程里的"平台风格/写法规范"类提示词很适合沉淀为 skill，按需加载省 context；属加分项，非迁移阻塞项。

---

## Go / No-Go 总结论

**Go 标准：a/b/c/d 四项全部可达成（允许写少量插件代码）→ 判定：Go ✅**

| 项 | 结论 | 关键依据 |
|---|---|---|
| a server 形态 | ⚠️→可达 | 无原生端点；Python SDK 常驻进程 + ~120 行 FastAPI 薄层**实测跑通** `/chat/completions`+`session_id` |
| b SSE 流式 | ⚠️→可达 | reasoning-delta/text-delta/usage 事件齐全，映射后实测 102 条 reasoning_content + usage chunk |
| c MCP 挂接 | ✅ | FastMCP stdio 实测挂通；`toolCallTimeoutMs: 1800000` 生效；qvp_mcp 本体待环境补齐后复测 |
| d 会话记忆 | ✅ | 同 session_id 暗号实测通过；落盘 `$DSH_HOME/sessions/`，路径由 DSH_HOME/dsh_home 显式指定，可挂卷 |
| e 模型主备 | ❌ 原生无 | 仅同路由重试（max 5 次）；serve 层自行 failover（与 failover.py 同构）即可覆盖 |
| f 并发上限 | ⚠️ 无原生 | serve 层信号量自实现；dsh 内部旋钮（maxParallelToolCalls 等）另有用途 |
| g skill 机制 | ✅ | `.dsh/skills/<name>/SKILL.md` 实测加载 |

**理由：** a/b 的"⚠️"不是能力缺失而是**形态差异**——dsh 把"对外协议"留给部署方，spike 已用薄层实证 OpenAI 兼容 + SSE + session_id 全部还原；c/d 原生即满足。Go 标准四项均可达成。

**薄 serve 层工作量估计：**
- MVP（本 spike 的 serve.py 水平：/chat/completions + SSE 映射 + session_id 透传）：**已完成原型，工程化约 0.5 人日**（错误处理、超时、日志）。
- 生产化（failover 主备切换、并发信号量、优雅重启、与 src/gateway 接口对齐、docker 化 + DSH_HOME 卷）：**约 1.5–2.5 人日**。
- 合计 **2–3 人日**；另需 **0.5 人日** 在依赖齐全环境补 qvp_mcp 本体挂接实测与 Kimi 备路实测。

**主要风险/遗留：**
1. qvp_mcp 本体未实测（本机无 code/.venv）——挂接机制已证，本体兼容性（FastMCP 版本、30 分钟长调用）待验。
2. Kimi 备路协议（`api.kimi.com/coding` 走 anthropic-messages 还是 openai-completions）未实测。
3. dsh 处于 developer preview，官方明示 "THERE WILL BE COMPATIBILITY-BREAKING CHANGES"——升级需钉版本并回归。
4. 长会话 compaction（上下文压缩）行为与 Nanobot 不同，迁移后需观察长任务输出稳定性。

**实验文件清单（../spike-dsh/）：** `mcp.patch.yml`（MCP+模型 patch）、`dummy_mcp.py`（FastMCP dummy）、`serve.py`（OpenAI 兼容薄层原型）、`sdk_memory_test.py` / `sdk_events_test.py`（记忆/事件流实测脚本）、`events-dump.txt` / `sse1.txt` / `sse2.txt`（原始证据）、`headless-default.yml`（headless 默认配置树）。

---

## 补测记录（2026-09-05，dsh_serve 组件化后真实环境补测）

前置条件：`code/.venv` 已建、`code/.env`（含全部密钥）已就位。密钥经
pydantic 从 `.env` 直接读取，全程未写入任何日志/输出。被测组件：
`code/dsh_serve/`（27 项单测全绿后做的真实链路实测）。

### 补测 1：qvp_mcp 本体挂接 ✅ 通过

配置：`MCP_COMMAND=code/.venv/Scripts/python.exe`、`MCP_ARGS=["-m","qvp_mcp"]`、
`MCP_PYTHONPATH=code/ 根`、`DSH_WORKSPACE=code/ 根`（qvp_mcp 的 src.config 靠
cwd 相对路径读 `.env`）、`toolCallTimeoutMs=1800000`、
`MCP_EXTRA_ENV={"LITELLM_LOCAL_MODEL_COST_MAP":"true"}`。

- 请求：`POST /v1/chat/completions`，session=mcp-live-002，要求调用
  `mcp__qvp__web_search(query="2026年中国新能源汽车销量", task_id="mcp-live-002")`。
- 证据（dsh 会话日志 `sessions/.../mcp-live-002--r1/session.jsonl.zstd` 解压）：
  - `tool/call` 事件：`"name": "mcp__qvp__web_search", "arguments": "{\"query\": \"2026年中国新能源汽车销量\", \"task_id\": \"mcp-live-002\"}"`
  - 最终回答返回真实检索结果标题：`新华社权威快报丨上半年我国新能源汽车产销量双超700万辆`
- 回调 fail-open ✅：实测时后端 127.0.0.1:8003 确认无服务（curl 000），
  qvp_mcp 的配额申请（quotas.py）与成本回调（cost_report.py）均为
  fail-open 设计——工具在回调不可达的情况下正常返回搜索结果，未被阻塞。
- 模型选择工具的注意点：dsh base 自带 `web_search`（DeepSeek 检索提供方），
  不显式点名 `mcp__qvp__web_search` 时模型可能用内置工具——生产 prompt 里
  应明确工具名，或在 patch 中禁用 base 的 tool-web 行。

**过程中发现并修复的问题（已进组件代码）：**
1. qvp_mcp 冷启动 import 在本机 OneDrive 目录约 52–88s（litellm 远程价表
   拉取超时 + 云盘 IO），逼近/超过 MCP SDK 60s 握手上限，首次挂接失败、
   靠 mcp-client 自动重连（指数退避）在 1–2 分钟内补上。缓解：
   `LITELLM_LOCAL_MODEL_COST_MAP=true` + `DSH_INITIALIZE_TIMEOUT_SECONDS=120`
   （新增配置项，默认 120s）。
2. 主/备两个 dsh 子进程并发首启竞争 `dsh-home/profiles/node_modules.lock`
   → 备路由启动报 atomic-write lock 超时。组件已加 `_boot_lock` 首启串行化。
3. 首启超时被强杀后 `profiles/node_modules` 留下半成品，下次启动报
   "not a dsh-managed module proxy" → 删该目录即恢复（已写进 README 遗留）。
4. 路由启动失败原先会被永久缓存（本进程内不再重试）→ 改为收尸半启动
   子进程、允许下一请求重试，错误仅作诊断展示在 /health。

### 补测 2：Kimi 备路 failover ✅ 通过

方法：环境变量覆盖 `DEEPSEEK_BASE_URL=http://127.0.0.1:9`（死端口，连接即拒），
不改代码；`MCP_ENABLED=false` 隔离变量。kimi 路由经 `$DSH_HOME/settings.yaml`
注册为 llm-pi-ai 自定义 provider（`api: anthropic-messages`，
baseURL `https://api.kimi.com/coding`，model `k3`）。

- 请求：session=failover-live-001，stream=false，普通问答。
- 服务日志（脱敏）：

  ```
  INFO  route started route=primary model=deepseek-v4-pro
  WARN  route failed route=primary session=failover-live-001
        error=dsh turn/end reason=error, route=primary:
        DeepSeek API request to http://127.0.0.1:9 failed
  INFO  route started route=fallback model=k3
  INFO  chat done session=failover-live-001 route=fallback model=k3
        degraded=True elapsed=12.78s tokens=7526/64 finish=completed
        original_error=dsh turn/end reason=error, route=primary: ...
  ```

- 响应：HTTP 200，总耗时 41s（含 dsh 进程冷启动；纯 failover 轮 12.78s，
  其中主路由失败判定约 17s 含 dsh 内部重试），`model=k3`，正文为正常
  中文回答，usage 齐全（prompt 7526 / completion 64）。
- 结论：主路由连接失败 → 同 session 自动降级 kimi k3（anthropic-messages
  协议实测可用）→ 成功返回，`degraded` 与 `original_error` 留痕正确。

### 补测后结论更新

原遗留风险 1（qvp_mcp 本体未实测）与 2（Kimi 备路协议未实测）均已闭环。
dsh_serve 组件两项真实链路实测全通过。

### 补测 3：真实任务空响应事故根因分析与修复（2026-09-05 联调冒烟）

**现象**：后端全链冒烟，agent_production 大节点（session=qvp-task-6518531b-…，
带 qvp_mcp 工具循环 + 长契约 JSON）主路由跑约 70s 后薄层判空响应，
failover 正确切 k3 完成（162s，tokens 29127/2750，任务全绿）。

**根因（会话日志铁证）**：解压
`dsh-home/sessions/.../<session>/session.jsonl.zstd`：
- 仅 1 个 turn / 1 个 step，**0 次工具调用**；
- `assistant/message` 只有 reasoning 块（20934 字符），**正文 0 字符**；
- usage：`outputTokens=8192, reasoningTokens=8192`——**输出预算被推理全部
  吃光**；
- `turn/end reason = {"kind": "max-tokens"}`。

即：deepseek 推理模型的 `max_tokens` **包含 reasoning_tokens**；薄层沿用
nanobot 旧值 `DSH_MAX_TOKENS=8192`，reasoningEffort=high + 12.9k 输入的长
prompt 下推理一轮就烧完 8192，模型还没来得及调工具/写正文就被截断。
薄层 EmptyResponse→降级行为本身正确（假设 e 的判空逻辑不是过敏，是
真的空）。

**候选假设排除**：a) MCP 工具报错→否（0 次工具调用）；b) 超 maxTokens→
**确认**（且是 reasoning 吃光预算的形态）；c) 内容过滤拒答→否（无拒答文本，
finish≠stop）；d) 轮次/时间预算→否（单 turn 单 step 正常结束于
max-tokens）；e) 薄层判空过敏→否（正文确实 0 字符）；f) 契约 JSON 截断→
否（根本没到写正文阶段）。

**修复（dsh_serve 内）**：
1. `DSH_MAX_TOKENS` 默认 8192→**32768**（config.py，环境变量可覆盖）；
2. `finish_reason=="max-tokens"` 新增为显式失败形态 `TruncatedOutputError`
   ——即使带出半截正文也判废触发降级（契约 JSON 截断即下游解析事故），
   错误信息注明 max_tokens 当前值与"含 reasoning_tokens"提醒；
3. 空响应错误信息带 finish 原因（日志可一眼区分 max-tokens 空与真空）；
4. README 同步（默认值、失败判定清单、流式纪律描述对齐缓冲设计）。

**验证**：
- 单测 34/34 全绿（新增 max-tokens 截断降级、半截正文不出流、错误信息
  含 max-tokens 三个用例）；
- 真实调用：重启服务后 POST 缩小版 agent_production 任务（web_search 取证
  一次 + 200 字正文 + 完整契约 JSON），主路由 deepseek-v4-pro 37.7s 返回，
  `degraded=False`，usage 13510/1554，契约 JSON 9 字段齐全可解析
  （pages=6、evidence=3 来自真实检索、draft 160 字）。

**残留风险**：
- 32768 对更长任务仍可能被推理吃光（推理预算与正文的配比不可控）；若再
  出现 max-tokens 降级，优先考虑调低 `DSH_REASONING_EFFORT` 或继续上调上限。
- 降级到 k3 后长任务首 token 延迟 = 主路由整轮耗时（当前推理烧完预算约
  70s 才发现），属结构性成本，暂接受。
- 诊断工具 `dsh_serve/analyze_session.py`（会话日志分析）与
  `verify_contract.py`（契约冒烟）留档复用。
