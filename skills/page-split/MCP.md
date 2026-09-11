# page-split 独立调用

> 本包 SKILL.md 正文即 LLM 提示词模板（src/gateway/skill_loader.skill_body 原样读取后直接喂模型），为避免文档混入提示词，独立调用说明单独放在本文件。

对应 MCP 工具：`page_split`（qvp_mcp v2 能力工具，实现见 `qvp_mcp/tools_capabilities.py`）。与流水线 page_split 节点共用同一实现（nodes.run_page_split_llm）：LLM 按页写文案 → 字数/均衡校验（契约见 contract.txt），不合格带意见重试一次；LLM 解析/调用失败自动退回机械切割（source=mechanical）。独立调用不写 page_copies 表。

参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| draft | str | 是 | 整篇正文 |
| task_id | str | 是 | 配额申请与成本记账键 |

返回：`{"ok": bool, "data": {...} | null, "error": str | null}`；data 含 `pages`（6 条）、`model_version`、`source`（"llm" | "mechanical"）、`balance_issue`（字数均衡校验结论，空串=合格）、`cost_cny`。

配额：LLM 类，每任务默认 5 次（settings.mcp_max_llm_tools_per_task）。

最小调用示例：

```json
{"draft": "整篇正文……", "task_id": "demo-001"}
```
