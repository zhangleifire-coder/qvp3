# dsh（8005/qvp3）vs Nanobot（8003/qvp2）创作 Agent 底座对比分析

> 日期：2026-09-07。纯分析文档，不含密钥。
> 数据来源：qvp2-postgres / qvp3-postgres 只读查询；本地 `nanobot-sessions-backup/`（82 个 jsonl，2026-08-22 ~ 09-03）；本地 `dsh-home/sessions/`（4 条任务会话，09-05）；服务器 qvp3-app `/data/dsh/sessions/`（1 条任务会话 c7c11ed3，09-07，只读拷贝分析）。
> 变量控制：两边跑同一套后端代码与同一套 skills/ 提示词（qvp3 只是把 `NANOBOT_BASE_URL` 指向容器内 dsh_serve :8901，见 `deploy/compose/qvp3.docker-compose.yml:46`），唯一变量是 Agent 底座。

## 一、产出物对比（DB 实测）

### qvp2（Nanobot 生产，近 7 天 review 任务，n=12）

| Query | 正文字数 | agent_production 成本(元) | 耗时(s) | 风险 |
|---|---|---|---|---|
| 西高新那家面馆好 | 475 | 2.52 | 1232 | green |
| 狗狗吃红枣有什么好处和功效 | 627 | 2.47 | 745 | yellow |
| 猫打完狂犬病疫苗的看护 | 606 | 4.93 | 999 | yellow |
| 真我GT8 Pro和红米K80 Pro对比 | 582 | 2.49 | 1063 | yellow |
| 狗狗吃红枣有什么好处和功效 | 596 | 3.29 | 197 | yellow |
| 小产权房和自建房的区别 | 467 | 2.47 | 254 | yellow |
| 台球杆大头中头小头区别 | 574 | 3.29 | 247 | green |
| 台球杆握法与击球发力教学 | 553 | 2.47 | 448 | yellow |
| 怎么判断榴莲有没有坏 | 822 | 2.45 | 500 | yellow |
| 无糖茶和普通茶有什么区别 | 723 | 2.48 | 767 | yellow |
| 为什么现在大家都不愿意喝含糖饮料了 | 531 | 2.47 | 433 | yellow |
| 新车淋雨后怎么处理防锈 | 536 | 2.47 | 270 | green |
| **均值** | **591** | **2.97** | **648** | green 3 / yellow 9 |

分页合规（80-130 字/页、任两页差 ≤40，契约单点 `skills/page-split/contract.txt`）：
- 09-03 契约收紧后：72 页中 68 页合规（94%），1 个任务失衡（无糖茶，极差 51）。
- 证据：12 任务共 29 claims / 87 evidence 条（含 ref_collect 节点写入）；cross_check 兜底 OCR 174 条。
- 注：分页文案由独立节点 `node_page_split` 的直连 LLM 生成（`src/pipeline/nodes.py:412`，带一次"不合格重试"），**与 Agent 底座无关**，两栈同口径。

### qvp3（dsh，服务器）+ 本地 dsh-home（冒烟）

| 来源 | Query | 模式 | 正文字数 | Agent 产出 pages 字数 | evidence | 成本(元) | 耗时(s) | 风险 |
|---|---|---|---|---|---|---|---|---|
| qvp3 c7c11ed3 | 新手第一台咖啡机怎么选 | general | 790 | 96/134/100/95/116/133（2 页超 130） | 3 | 2.66 | 764 | yellow |
| 本地 c3ae94be | 新手第一台咖啡机怎么选 | general | 635 | 91/124/93/112/122/95（全合规） | 4 | — | — | — |
| 本地 053cb8de | 德龙EC685实测 | single | 487 | 88-109（全合规） | 4 | — | — | — |
| 本地 792ea1e9 | 德龙vs柏翠 | compare | 532 | 93-126（全合规） | 4 | — | — | — |

### 结论

1. **"dsh 正文偏短"在本轮数据中不成立**：同 query（新手第一台咖啡机怎么选）dsh 两次产出 635/790 字；qvp2 同期 12 条均值 591（区间 467-822）。先前观察到的 695 vs 529-635 落在 Nanobot 自身方差内（09-03 后 Nanobot 契约 JSON 内 draft 实测 509-634）。样本小，建议以 ≥20 条同批 query 复测再下结论。
2. **分页合规率两栈相当**（dsh 本地 3 条全合规；qvp3 1 条 4/6 合规），且最终 page_copies 由独立节点兜底，不随底座漂移。注意 qvp3 最终入库 page_copies 有 2 页 134/133 超上限——qvp2 也有 139 案例，属 page-split 节点"保留违规较轻版"兜底策略的共有现象，非 dsh 差距。
3. 成本/耗时/风险分布：单样本（2.66 元 / 764s / yellow）落在 qvp2 分布内，无显著差异。成本口径问题见第四节（dsh 侧 LLM token 被低估）。

## 二、Agent 行为对比（会话 transcript 级）

### Nanobot（本地备份 74 条任务会话）

| 指标 | 分布 |
|---|---|
| 助手轮数 | 典型 3-7 轮 |
| 工具调用 | web_search 1-2 次、generate_images 1 次为基线；single/compare 模式加 image_search 3-4 次；ocr_image 0-2 次（09-03 后新会话全部为 0，符合"默认跳过"指令） |
| 契约一次通过率 | **20/74（27%）被后端"未通过系统校验"打回重问**（典型原因：draft 缺失/pages 0 条/images 0 项——最终 JSON 被 maxTokens 8192 截断）；另有 6/74（8%）触发网关 "Output limit reached" 自动续写 |
| 推理量 | 09-03 会话 reasoning_content 合计约 3 万字符/任务 |
| 越界工具 | 部分会话使用 exec/write_file/read_file/list_dir/grep/find_files（Nanobot 自带工具），个别会话工具数达 30+ |

### dsh（4 条本地 + 1 条服务器会话）

| 会话 | 步数 | MCP 工具 | dsh 内置工具调用 | 推理字符 | 输出 token 合计 |
|---|---|---|---|---|---|
| 本地 053cb8de | 3 | web_search×2, generate_images×1 | 无 | 35,023 | 19,739 |
| 本地 792ea1e9 | 6 | web_search×2, generate_images×1 | read×2, **write×1（把契约 JSON 写入 workspace 的 output_*.json）** | 54,150 | 33,557 |
| 本地 c3ae94be | 6 | web_search×2, generate_images×1 | **pwsh×2（数字数+一次 `Write-Output "hello"` 空探）**, read_image×2 | 36,958 | 20,194 |
| 服务器 c7c11ed3 | 7 | web_search×2, generate_images×1 | **bash×4（python 数字数×2 + `echo done` 占位×2）**, approval 升级尝试×2（outcome=unavailable） | 78,531 | 36,055 |
| 本地 6518531b（修复前实证） | 1 | 0 | 无 | 20,934 | 8,192（finish=max-tokens，正文 0 字符） |

### 结论

1. **契约一次通过率 dsh 反而更好**：5/5 最终输出均为完整契约 JSON、字段齐全，无后端重问；根因是 `DSH_MAX_TOKENS=32768` 修复后不再截断。Nanobot 的 8192 上限是其 27% 重问率的主因。
2. **dsh 内置工具（bash/pwsh/read/write/read_image）打开了 Nanobot 没有的"侧信道"行为**：
   - 正面：Agent 用 bash/pwsh 精确数字数以满足 80-130 字契约（这是 pages 合规率的贡献因素）；
   - 负面：写 workspace 文件、`echo done` 占位调用、沙箱升级尝试（serve 模式 approval policy=ask 且无审批通道 → outcome=unavailable，白烧 2 步）；`read_image` 替代 `mcp__qvp__ocr_image` 做图文自检——**绕过 MCP OCR 配额（≤8/任务）与 tool_ledger 成本记账**，且自检结果不进契约 ocr_texts，后端兜底 OCR 照跑（qvp3 ocr_results=6），等于重复劳动。
3. 推理量 dsh 偏高（3.5-7.9 万字符 vs Nanobot 约 3 万），与 reasoningEffort=high + 更大 maxTokens 预算一致；单步 output 最高 16.5k token（c7c11ed3 step3），在 8192 口径下必然截断。
4. OCR 行为：两边均遵守"默认跳过"（dsh 5 条会话 ocr_image 0 次，唯一 1 次出现在 k3 备路由会话），后端兜底机制两栈一致生效。

## 三、参数口径对照表

| 项 | Nanobot（`nanobot/config.json` + `deploy/nanobot.config.server.json`） | dsh（`dsh_serve/config.py` + `sdk_runner.py` + 实测 request/header） | 差异判定 |
|---|---|---|---|
| 主/备模型 | deepseek-v4-pro / k3 | 同 | 对齐 |
| temperature | **0.7（preset 显式）** | **不可配**——deepseek_harness SDK 全无 temperature 参数（grep=0），走模型默认（1.0） | **dsh 缺失** |
| maxTokens | 8192 | 32768（`dsh_max_tokens`，已修复） | 有意差异，dsh 更优（消掉 27% 截断重问） |
| reasoningEffort | 未配（API 默认；09-03 会话 reasoning≈3 万字符，实际等同 high） | 未配，但 dsh base 默认注入 **high**（request/header 实测） | 实践对齐，口径来源不同 |
| context window | 131072（preset 显式） | **1,000,000**（dsh 模型注册表默认值，request/context 实测） | **dsh 口径错误**：超出真实窗口后 compaction/截断阈值全部失真（当前峰值 68k，未爆雷） |
| compaction | idleCompactAfterMinutes=10 + 131072 窗口触发 | 任务会话单轮短生命周期，未观察触发；且按 1M 窗口算阈值，实际不会触发 | 潜在缺口（长会话/记忆场景） |
| 工具超时 | toolTimeout 1800000ms | mcp_tool_timeout_ms 1800000 | 对齐 |
| 容错语义 | failOnToolError=false | failOnStartupError=false | 对齐 |
| 子代理 | maxConcurrentSubagents=1 | 未配置、5 条会话均未使用 | 未对齐但无实证影响 |
| 并发 | 网关原生并发（09-03 实测 ≥2 个 agent_production 时间重叠；后端 MAX_CONCURRENCY=4） | 信号量 4，但 **`sdk_runner.py:167` 每路由一把锁串行** → 主路由有效并发=1 | **dsh 吞吐缺口** |
| 沙箱/审批 | 无沙箱门禁（exec 直接可跑） | workspace-write + approval policy=ask；serve 无审批通道 → 升级尝试 outcome=unavailable 浪费轮次 | **dsh 新增摩擦** |
| 内置 web 工具 | —（已被 MCP 覆盖语义） | patch 禁用 tool-web（`disable_builtin_web_tools`） | 已对齐 |
| usage 口径 | 网关 SSE usage（后端取最后一个非零 chunk） | dsh 每 step 一条 usage 事件，**last-chunk-wins → 只记最后一步**：c7c11ed3 记录 completion=3,797，实际全 session 输出 36,055（≈10 倍低估；按平直价折合文本成本实记 0.22 元 vs 全量口径 1.11 元） | **dsh 记账失真** |
| model_version 标签 | `nanobot:<model>` | dsh 产出在 DB 也标 `nanobot:deepseek-v4-pro`（`nanobot_client.py:132` 前缀硬编码，qvp3 c7c11ed3 实证） | 可观测性缺口 |
| 会话持久化 | 容器重建即蒸发（qvp2 历史缺陷） | dshhome 持久卷 + fork 别名冷恢复（`failover.py:141`） | **dsh 更优** |

## 四、改进清单（按影响排序）

| # | 差距 | 修法 | 工作量 | 风险 |
|---|---|---|---|---|
| 1 | 主路由有效并发=1（route.lock 串行），8005 吞吐天花板低于 8003 | `sdk_runner.py`：每路由从"单 harness + 锁"改为"N 个 harness 实例池"（N=DSH_MAX_CONCURRENT），或每 session 独立子进程；注意首启串行 `_boot_lock` 逻辑需保留（node_modules.lock 竞争） | 中（1-2 天） | 中：进程内存×N；会话落盘并发写需验证 |
| 2 | dsh 内置 bash/pwsh 在 serve 模式走 approval=ask → unavailable，浪费轮次且占位调用烧 token；write 工具把契约写到 workspace 文件属无效动作（**已修复 2026-09-07**：patch 禁用全部 23 行内置工具，见 dsh_serve README「内置工具禁用」） | serve patch 层把 shell/write 类工具置 disabled（参照 tool-web 先例），或在 SDK 启动参数把 approval policy 改为自动拒绝并保留 read/read_image；数字数需求可由提示词引导用"输出前自数"而非 shell | 小（半天，纯 patch 配置） | 低：pages 合规率可能略降，需回归 3 条冒烟 |
| 3 | `read_image` 替代 `mcp__qvp__ocr_image`，绕过 OCR 配额与 tool_ledger 记账，且自检结果不进 ocr_texts（**已修复 2026-09-07**：read_image 随 tool-fs 行一并禁用） | patch 禁用 base vision 工具（若有 stable id），或在 `_AGENT_INSTRUCTIONS` 明确"图文自检只允许调 ocr_image" | 小 | 低 |
| 4 | contextWindow=1,000,000 与真实 131,072 不符，compaction/截断保护阈值失真（**已修复 2026-09-07**：settings.yaml 双路由覆盖 131072） | 查 dsh serve 是否支持 per-model contextWindow 覆盖（settings.yaml 模型注册条目），配 131072；若不支持，向 dsh 上游提 issue | 小 | 低 |
| 5 | temperature 0.7 无法对齐（SDK 无参数，跑默认 1.0），文风/稳定性口径与生产不一致（**确认放弃 2026-09-07**：initialize schema 无 temperature，serve 链路无注入点；README 已注明验收口径差异） | 短期：接受并在验收标准里注明；中期：给 deepseek-harness SDK 加 temperature 透传（client.py payload 构造点明确，改动数行） | 中（依赖上游或 fork） | 低 |
| 6 | usage last-chunk-wins 低估 LLM 成本约一个量级（**已修复 2026-09-07**：逐条累加 + 发累计值；看账人需知悉口径修正） | `dsh_serve/failover.py` StreamAggregate 改为累加各 step usage（注意 cacheRead 口径），最终 chunk 发累计值；`nanobot_client.py` 消费端不变 | 小（半天） | 低：node_events 成本会"变高"，需同步告知看账人 |
| 7 | dsh 产出 model_version 标成 `nanobot:...`，灰度期间无法按栈筛选质量数据（**已修复 2026-09-07**：model=`dsh:<模型名>`；cost_tracker 子串匹配实测兼容，src/ 未改） | `dsh_serve` 返回 model 字段加 `dsh:` 前缀，或后端把前缀逻辑改为按网关类型配置 | 极小 | 低 |
| 8 | qvp3 服务器任务样本仅 1 条（compose 项目改名产生 qvp3_dshhome/qvp3_qvp3_dshhome 双卷，早期冒烟会话疑似随卷丢失） | 固化 compose project 名与卷名；放量跑 ≥20 条灰度任务补齐产出物样本 | 小 | 低 |

## 附：本次分析的执行口径

- qvp2/qvp3 均为只读：psql SELECT、docker logs、读会话文件；未触碰 8002/8000 栈。
- 本地为分析 dsh 的 zstd 会话日志，向 `code/.venv` 安装了 `zstandard` 包（分析脚本 `dsh_serve/analyze_session.py` 的既有依赖）。
- nanobot 备份语料（08-22~09-03）横跨分页契约收紧（09-02/03 提密度至 80-130 字），行为对比优先取 09-03 后的会话以匹配 dsh 侧提示词版本。

## 五、独立调用实测（qvp_mcp v2 × dsh，2026-09-07）

环境：`scripts/start-all-dsh.sh` 整栈（PG + dsh_serve:8901 + 后端:8003，
后端 NANOBOT_BASE_URL→8901）。dsh_serve 为 2026-09-07 改进后代码
（内置工具禁用、usage 累加、model 前缀、contextWindow 131072）。

### 工具目录（地面真值：会话 request/header 提取）

`tools(16)` = 15 个 `mcp__qvp__*`（cross_check / draft_write /
generate_images / image_search / ocr_image / page_regen / page_split /
page_subject / prompt_analyze / risk_classify / rule_check / text_draft /
visual_check / visual_write / web_search）+ `skill`。**无任何内置旁路工具**
（bash/fs/web/exit_plan_mode 均已消失）。

### 微任务（dsh 会话日志 tool/call + tool/result 铁证）

| 任务 | 工具调用（实参） | 返回摘要 | 耗时 |
|---|---|---|---|
| A 分页 | `mcp__qvp__page_split(draft=<600字正文>, task_id=mt-a-001)` | `ok:true`，pages=6 条、source=llm、balance_issue="字数不足80字：第1页仅78字"（校验真实生效） | 445s（含工具内 LLM 分页调用） |
| B 合规 | `mcp__qvp__rule_check(draft=<含"最便宜/第一/绝对"文案>, task_id=mt-b-001)` | `ok:true`，all_passed=false，未通过：word_count_400_700（46字）、no_absolute_words（检出 绝对/最/第一）；title_max_25 通过 | 5.2s |
| C 独立创作 | `mcp__qvp__draft_write(query="便携榨汁杯怎么选", task_id=mt-c-001)` | `ok:true`，869 字原创正文（经验分享风格，"用坏3台"第一人称），返回字段 text/model_version/prompt_version/cost_cny/degraded | 423s（含工具内 LLM 创作） |

注：A/C 的 HTTP 客户端 280s 超时先于服务端完成（服务端正常完成并落盘），
经同 session 追问取回结果——微任务/联调调用方超时应放宽（dsh_serve 侧
REQUEST_TIMEOUT_SECONDS=3000 无问题）。

### 配额/记账验证（回调真实到达，非 fail-open 静默丢失）

后端 `backend.log`：三次工具调用对应 **3× `POST /api/internal/quota_acquire`
200 + 3× `POST /api/internal/tool_usage` 200**，一一配对。上次补测（8003
不在线）验证了 fail-open 不阻塞；本次验证了在线时配额计数与成本台账
真实落账。两条路径均闭环。
