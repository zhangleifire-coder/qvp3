# page-subject 独立调用

> 本包 SKILL.md 正文即 LLM 提示词模板（src/gateway/skill_loader.skill_body 原样读取后直接喂模型），为避免文档混入提示词，独立调用说明单独放在本文件。

对应 MCP 工具：`page_subject`（qvp_mcp v2 能力工具，实现见 `qvp_mcp/tools_capabilities.py`）。与 services/page_subject 同一实现：一次 LLM 调用产出 6 个主体短语；解析失败/数量不符返回 subjects=null（流水线语义=沿用通用锚定条款，不算调用失败）。独立调用不写库。

参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| pages | list[str] | 是 | 6 页分页文案（顺序即页序） |
| task_id | str | 是 | 配额申请与成本记账键 |

返回：`{"ok": bool, "data": {...} | null, "error": str | null}`；data 含 `subjects`（6 条主体短语或 null）、`model`、`cost_cny`。

配额：LLM 类，每任务默认 5 次（settings.mcp_max_llm_tools_per_task）。

最小调用示例：

```json
{"pages": ["第1页文案", "第2页文案", "第3页文案", "第4页文案", "第5页文案", "第6页文案"], "task_id": "demo-001"}
```
