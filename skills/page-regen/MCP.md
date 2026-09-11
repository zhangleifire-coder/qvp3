# page-regen 独立调用

> 本包 SKILL.md 正文即 LLM 提示词模板（src/gateway/skill_loader.skill_body 原样读取后直接喂模型），为避免文档混入提示词，独立调用说明单独放在本文件。

对应 MCP 工具：`page_regen`（qvp_mcp v2 能力工具，实现见 `qvp_mcp/tools_capabilities.py`）。与 services/regen.py 定点重生成共用同一实现（rewrite_page_copy），独立调用不写 page_copies 表。

参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| task_id | str | 是 | 配额申请与成本记账键 |
| page_index | int | 是 | 要重写的页码（1-6） |
| old_copy | str | 是 | 该页现有文案 |
| draft_body | str | 是 | 整篇正文（供模型对齐上下文与事实） |
| feedback | list[str] | 否 | 审核/修改意见列表，逐条注入 |
| sibling_pages | list[dict] | 否 | 其余各页现状 [{"page_index": 2, "body": "..."}]；提供后模型按每页 80-130 字且与各页相差≤40 字对齐 |

返回：`{"ok": bool, "data": {...} | null, "error": str | null}`；data 含 `page_index`、`body`（新文案）、`model_version`、`cost_cny`。

配额：LLM 类，每任务默认 5 次（settings.mcp_max_llm_tools_per_task）。

最小调用示例：

```json
{"task_id": "demo-001", "page_index": 3, "old_copy": "原第3页文案……", "draft_body": "整篇正文……"}
```
