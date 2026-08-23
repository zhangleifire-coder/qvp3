# 改造说明 · Nanobot 全链创作 Agent + 业务调度层（2026-08-22）

> 改造目标：AI 推理与工具执行的工作流交给 Nanobot Agent 调度；业务后端（FastAPI）
> 保留参数校验、数据库、重试、失败告警、任务状态持久化，用传统软件工程层兜底稳定性。
> 分支：`main`（基线 `6d938fa` 为移交包 20260822 原始状态；`6d9de57` 起为本次改造）。

---

## 一、目标架构

```
┌─ 业务调度层（FastAPI 后端，全部保留）────────────────────────────┐
│ 导入/参数校验 → 任务入库(tasks) → 队列调度(AIMD 自适应并发)        │
│ → agent_production 大节点 ──失败→ 重试(幂等)/任务failed/监控告警   │
│ → rule_check → cross_check → risk_classify → review → 导出        │
│ 成本核算(node_events) / SSE 实时监控 / 审核工作流 / 操作审计        │
└──────────────┬──────────────────────────────────────────────────┘
               │ 一次 OpenAI 兼容流式调用（POST /v1/chat/completions）
               ▼
┌─ Nanobot 创作 Agent（nanobot serve，127.0.0.1:8900）─────────────┐
│ 模型路由：deepseek-v4-pro 主 / kimi k3 备（fallbackModels）        │
│ 工具（qvp_mcp，MCP stdio 子进程）：                                │
│   web_search(豆包) · image_search(OpenSERP)                       │
│   generate_images(gpt-image-2 批量+去重+本地化) · ocr_image(qwen)  │
│ 编排：检索取证 → 写正文 → 分页文案 → 生成6图 → OCR 自检            │
│ 输出：严格 JSON 契约 {evidence, draft, pages[6], images[6], ...}   │
└──────────────────────────────────────────────────────────────────┘
```

**流水线双路径**（`AGENT_PIPELINE_ENABLED` 开关）：

| 路径 | 节点序列 | 说明 |
|---|---|---|
| Agent 路径（新，默认开） | task_import → **agent_production** → rule_check → cross_check → risk_classify → review_queue → batch_signoff → publish_snapshot（8 节点） | 创作段六节点收敛为一个 Agent 大节点 |
| 直连路径（原，兜底） | 原 13 节点 | Nanobot 故障时 `.env` 改 `false` 重启即回退 |

两路径共用：幂等（node_events sha256 键）、成本、SSE 监控、审核、驳回重生成。
`/api/meta/nodes` 按开关自适应返回节点清单（前端 StepsBar 唯一数据源，无需改前端）。

## 二、新增代码地图

```
code/
├── qvp_mcp/                      # MCP 工具服务器（nanobot 子进程，stdio）
│   ├── server.py                 #   4 工具：web_search/image_search/generate_images/ocr_image
│   ├── quotas.py                 #   task_id 级硬配额（生图≤8/搜索≤3/OCR≤8），超限报错
│   └── cost_report.py            #   每次工具调用成本 POST 回调后端
├── nanobot/
│   ├── config.json               # nanobot 配置（providers/presets/fallback/MCP/密钥走 ${ENV}）
│   └── start-nanobot.sh          # 启动脚本（从 .env 注入 key，--timeout 2400）
├── src/gateway/nanobot_client.py # OpenAI 兼容流式客户端（session 隔离/超时/usage/纠错重问）
├── src/gateway/tool_ledger.py    # 工具成本内存台账
├── src/api/internal.py           # POST /api/internal/tool_usage（token 鉴权）
├── src/pipeline/agent_production.py  # 创作大节点（核心，见下）
├── src/pipeline/orchestrator.py  # 双路径调度
├── src/stream/progress.py        # agent_production 标签/agent_progress 过程事件
├── scripts/smoke_agent.py        # 冒烟：导入→轮询→产物摘要
├── scripts/smoke_review.py       # 冒烟：通过/驳回/带反馈重生成
└── tests/unit/test_nanobot_client.py / test_agent_output.py / test_quotas.py
└── tests/integration/test_agent_pipeline.py   # 全 mock，4 用例
```

## 三、agent_production 节点工作流

1. **前置健康检查**：Nanobot `/health` 不通 → 节点立刻失败（错误信息提示启动 Nanobot 或回退开关）。
2. **上下文组装**：query + mode + 提示词库三模板（draft_gen/page_split/image_gen，
   解析顺序：用户自定义 → admin 系统覆盖 → 代码默认，**提示词库仍然后端主管**）
   + 驳回反馈（重生成轮次）+ 输出 JSON 契约 + task_id（配额记账用）。
3. **流式调用**：`session_id = qvp-task-{task_id}-{rand}`（任务间隔离；纠错追问复用同 session）。
   过程文本按 400 字节流转发 `agent_progress` 事件到监控页。
4. **契约校验**：防御解析（剥 ```json 围栏/截大括号）+ 严格校验
   （draft≥150 字 / pages=6 非空 / images=6 带 URL / evidence≤12）。
   失败 → 同 session 纠错重问一次 → 再失败 → 节点失败（幂等重跑兜底）。
5. **确定性收尾**（后端职责，Agent 不碰数据库）：
   - 图片全部本地化（内容 md5 去重基准、3:4 尺寸校验、`static/generated/` 落盘）；
   - 参考图（compare/single）本地化存 official 素材；
   - 原子落库：claims/evidence、drafts、page_copies×6、assets×6(+refs)、ocr_results×6；
   - OCR：Agent 自检结果优先，缺失则后端兜底补齐（cross_check 依赖）。
6. **成本合并**：文本 usage（流式无 usage 时按字符估算）+ MCP 工具回调台账
   → node_events.cost_estimate_cny（成本明细页口径不变）。

## 四、可靠性设计（软件工程层兜底）

| 风险 | 兜底机制 |
|---|---|
| Nanobot 进程崩溃 | 前置 health 检查 + 读超时 1800s + 节点失败 → 任务 failed → 幂等重试；`AGENT_PIPELINE_ENABLED=false` 秒级回退 13 节点直连 |
| Agent 输出不合格 | JSON 契约严格校验 + 同 session 纠错一次 + 两次失败节点失败重跑 |
| Agent 失控烧钱 | **MCP 工具配额（后端权威）**：agent_production 每次开始时 reset，工具调用前 MCP 经 `POST /api/internal/quota_acquire` 向后端申请（生图≤8 张/搜索≤3/OCR≤8）；中断/失败后续跑自动恢复全额预算（2026-08-23 修复：原 MCP 进程内存计数不释放，被中断任务续跑时配额被锁死） |
| 成本记账漂移 | 工具每次调用 HTTP 回调后端台账；文本 usage 缺失按字符估算；node_events/成本明细页口径不变 |
| 任务半截状态 | 产物落库为一次原子提交；图片本地化在落库前完成，失败即节点失败不留半截产物 |
| 长任务人工止损 | `POST /api/tasks/{id}/cancel`：排队中→出队；生产中→取消执行协程（CancelledError 不经过 execute_node 的异常处理，节点事件自动回滚）；任务转 `cancelled`，重试幂等续跑。注意：Nanobot 侧 Agent 当轮推理会跑完（配额兜底） |
| 调度崩溃恢复 | scheduler `_recover_pending` 不变（draft/processing 重置重入队；cancelled 不自动重跑，可手动重试） |
| 长连接断连 | 后端↔Nanobot 流式 SSE 保活；后端↔前端 SSE 原样保留 |

### 实时监控（2026-08-23 增强）

- 进行中任务按**业务阶段条**展示（8 节点 chips：完成/进行中/待做）
- Agent 子阶段实时可见：`agent_tool` 事件（检索证据 / 搜索实景参考图 / **生成配图 P3/6**（逐张）/ OCR 图文自检）
- **流式输出框**：Agent 生成内容实时滚动展示，带字符数与 token 估算（约 字符/1.7）
- 每任务卡「✕ 中断任务」按钮（确认后 cancel；任务中心也可对 cancelled 任务「继续生产」）

## 五、本地运行（Windows / Git Bash）

```bash
cd code/
# 0. 依赖（首次）
py -3.12 -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt

# 1. 起专用 PG（5432 被其它项目占用时映射 5433，.env 里 DATABASE_URL 同步改）
docker run -d --name qvp-postgres -e POSTGRES_USER=qvp -e POSTGRES_PASSWORD=qvp \
  -e POSTGRES_DB=qvp -p 127.0.0.1:5433:5432 postgres:16-alpine

# 2. 建表 + 账号（DATABASE_URL 显式传，init_db 不读 .env）
DATABASE_URL="postgresql+asyncpg://qvp:qvp@localhost:5433/qvp" \
  PYTHONUTF8=1 .venv/Scripts/python init_db.py

# 3. 起 Nanobot 网关（:8900，加载 nanobot/config.json + qvp_mcp 工具）
bash nanobot/start-nanobot.sh        # 前台；后台加 > nanobot/nanobot.log 2>&1 &

# 4. 起后端（:8000，.env 需含 AGENT_PIPELINE_ENABLED=true）
.venv/Scripts/python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000

# 5. 测试 / 冒烟
DATABASE_URL="postgresql+asyncpg://qvp:qvp@localhost:5433/qvp" \
  .venv/Scripts/python -m pytest -q            # 114 用例全绿
PYTHONUTF8=1 .venv/Scripts/python scripts/smoke_agent.py "某Query" general
```

登录：张三/李四/王五（1qaz@WSX）、admin（root_admin_1234）。
`MOCK_IMAGE_GEN=true` 时不调生图 API（本地联调不花钱，MCP OCR 返回占位文本）。

## 六、已知限制与后续项

1. **驳回定点重生成（partial_regen）仍走直连路径**：regen.py 未改（按计划第一版保留），
   带「⚑标记」的驳回由后端直连 litellm/生图完成；整体驳回（无标记）已走 Agent 带反馈重生成（已验证 `_regen1`）。
2. **OpenSERP 未起时**：compare/single 的搜图工具报错 → Agent 容错继续（参考图为空，纯文生图）。
   服务器部署用 compose 起 openserp。本地 Docker Hub 拉镜像受限（403），未验证真实搜图链路。
   生图线路已于 2026-08-23 切换为 linkai.pics 直连（linkai 实际单价未知，记账暂按 0.2 元/张）。
3. **流式 usage**：nanobot 流式响应不带 usage，文本成本按字符估算（usage_estimated 标记）。
4. **nanobot session 内存**：每节点执行一个 session，长期运行建议定期重启 nanobot（低峰期）。
5. **mock 生图下风险分级偏 red**：mock OCR 文本与文案对不上 → cross_check 误报，真实生图下正常。
6. **服务器部署（后续）**：docker-compose 增加 nanobot + qvp_mcp 服务（或同容器），
   config.json 中的绝对路径改容器内路径；密钥经 ${ENV} 注入。

## 七、验证记录（2026-08-22/23/24 本地）

### 性能优化（2026-08-24，实测 agent_production 560s → 277s，↓51%）

- **并行生图**：`IMAGE_GEN_PARALLEL=2`（6 张分 3 批并发调用生图 API，批间隔 1s；
  跨批内容去重保留，重复页串行重生；`1` 退回串行防限流）
- **并发对齐**：`NANOBOT_MAX_CONCURRENCY_REQUESTS=4` 与后端 MAX_CONCURRENCY 一致
  （消除第 4 个任务在 Nanobot 的隐形排队）；`idleCompactAfterMinutes=10` 及时压缩会话内存
- **OCR 策略**：Agent 指令改为默认跳过 OCR 自检（系统自动校验，省 30-60s/任务）；
  后端兜底 OCR 改 3 路信号量并发（30-60s → 15-25s）
- 吞吐估算：单任务 4.6 分 × 并发 2-4 ≈ **26-52 条/小时**（原 12-24）
- 观察点：linkai 在并发 4 下的限流表现（AIMD 自动降并发兜底），稳定后可再提
  IMAGE_GEN_PARALLEL 至 3 与并发上限至 6-8

### 真实生图链路（2026-08-23，linkai.pics 直连线路，OPENAI_IMAGE_BASE_URL=https://direct.linkai.pics/v1）

- API 探测：仅暴露 `gpt-image-2`，与平台模型配置一致；单张约 57s，返回 S3 预签名
  远程 URL（浏览器友好，非 openox 内联 URL），实测 1152x1536 精确竖版。
- 全真实冒烟①（修复前）：8 节点全绿 → review；6 张真图（1.8-2.3MB/张）全部本地化；
  暴露两问题——Agent 只自检 2/6 页 OCR 导致缺页被判「识别失败」拉红风险；
  正文「最」字触禁词规则。
- 全真实冒烟②（修复后，"新手第一辆车怎么选"）：**OCR 6/6 页真实识别**、
  **cross_check 6 过 0 失败**（图上文字与分页文案真实验证一致）、禁词规则通过、
  风险 red→yellow（仅剩超字数）；随后已在指令中加 400-700 字硬约束。
- 成本口径：image_cost_per_image_cny 仍按 0.2 元/张记账（linkai 实际单价未知，
  拿到账单后调 .env 即可）。

### mock 生图链路（2026-08-22）

- pytest：**114/114 全绿**（原 92 + 新增 22）
- 冒烟 general：8 节点全绿 → review；540 字正文 / 6 页 / 6 图 / OCR 兜底 / 3 证据；成本 ¥0.0652
- 冒烟 compare：677 字（agent_compare_v1），无 OpenSERP 容错通过
- 冒烟 single：722 字（agent_single_v1）
- 审核闭环：approve→approved；reject→rejected；retry→Agent 带反馈重生成（agent_compare_v1_regen1）
- 成本明细：/api/admin/costs 按任务/节点/模型三维拆分正常
- 回退开关：AGENT_PIPELINE_ENABLED=false → /api/meta/nodes 返回 13 节点直连路径
