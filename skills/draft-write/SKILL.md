---
name: draft-write
description: 正文生成（draft_gen）提示词：按 mode 三模式模板 + 人设化共享段 + 校稿润色。
notes: 2026-09-01 吸收 8002 人设化共享段（真人感/信息密度/标题公式/细节禁令/合规），仅系统默认模板追加；polish 为节点内二段校稿（700 字上限口径）。
---

片段文件：
- `prompts/general.txt`
- `prompts/single.txt`
- `prompts/compare.txt`
- `shared_persona.txt`
- `polish.txt`


## 独立调用

对应 MCP 工具：`draft_write`（qvp_mcp v2 能力工具，实现见 `qvp_mcp/tools_capabilities.py`）。与流水线 draft_gen 节点共用同一实现（nodes.run_draft_gen），独立调用不跑流水线、不写 drafts 表。

参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| query | str | 是 | 选题/创作要求全文 |
| task_id | str | 是 | 配额申请与成本记账键 |
| mode | str | 否 | general / single / compare，默认 general |
| feedback | list[str] | 否 | 此前的驳回/修改意见列表，逐条注入提示词要求修正（对应流水线驳回重生成路径） |

返回：`{"ok": bool, "data": {...} | null, "error": str | null}`；data 含 `text`（正文草稿）、`model_version`、`prompt_version`、`cost_cny`、`degraded`。配额拒绝/调用异常一律 ok=false + error 说明，不抛异常。

配额：LLM 类，每任务默认 5 次（settings.mcp_max_llm_tools_per_task）；成本按实际 usage 经 report_usage 回调后端记账。

最小调用示例：

```json
{"query": "选题：新手爸妈如何挑安全座椅", "task_id": "demo-001"}
```
