---
name: rule-check
description: 规则机校验（rule_check）独立调用：字数 400-700 / 标题≤25 字 / 绝对化用语 / 含安全承诺词需免责声明；确定性代码，不调 LLM。
notes: 2026-09-03 qvp_mcp v2 新增；与流水线 rule_check 节点共用同一实现（quality/rules.check_rules）。
---

功能说明：规则机校验是正文合规的第一道机检关卡，校验项包括正文字数 400-700、标题不超过 25 字、绝对化用语、含安全承诺词时必须有免责声明。任何正文草稿都可用它做快速合规自检；独立调用不写 rule_results 表。

## 独立调用

对应 MCP 工具：`rule_check`（qvp_mcp v2 能力工具，实现见 `qvp_mcp/tools_capabilities.py`）。

参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| draft | str | 是 | 正文全文 |
| task_id | str | 是 | 配额申请与成本记账键 |
| title | str | 否 | 标题；留空=取正文首行前 25 字（同节点口径） |

返回：`{"ok": bool, "data": {...} | null, "error": str | null}`；data 含 `rule_results`（[{"rule_name", "passed", "details"}]）、`all_passed`（bool）。

配额：校验类，每任务默认 10 次（settings.mcp_max_check_tools_per_task）；不调 LLM，成本记账 0。

最小调用示例：

```json
{"draft": "正文全文……", "task_id": "demo-001", "title": "安全座椅怎么选"}
```
