# prompt-analyze 独立调用

> 本包 SKILL.md 正文即 LLM 提示词模板（src/gateway/skill_loader.skill_body 原样读取后直接喂模型），为避免文档混入提示词，独立调用说明单独放在本文件。

对应 MCP 工具：`prompt_analyze`（qvp_mcp v2 能力工具，实现见 `qvp_mcp/tools_capabilities.py`）。与 /api/tasks/analyze_query 同一实现（本包提示词 → failover → 编号列表容错解析），用于组合生成导入前扩充问题池。

参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| query | str | 是 | 原始提问（≥4 字，过短返回 ok=false） |
| task_id | str | 是 | 配额申请与成本记账键 |
| count | int | 否 | 生成数（5-30，默认 20，越界自动收敛到区间内） |

返回：`{"ok": bool, "data": {...} | null, "error": str | null}`；data 含 `query`、`questions`（泛化补充问题列表）、`model`、`cost_cny`；模型未返回有效问题列表时 ok=false。

配额：LLM 类，每任务默认 5 次（settings.mcp_max_llm_tools_per_task）。

最小调用示例：

```json
{"query": "家里老人膝盖不好，上下楼费劲怎么办", "task_id": "demo-001", "count": 20}
```
