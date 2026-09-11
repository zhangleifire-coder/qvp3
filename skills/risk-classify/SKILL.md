---
name: risk-classify
description: 红/黄/绿风险分级（risk_classify）独立调用：规则校验+交叉校验结果+证据完备性 → 交付风险等级；纯函数，不调 LLM。
notes: 2026-09-03 qvp_mcp v2 新增；与 risk/classifier.classify 同一实现。
---

功能说明：已有规则校验/交叉校验结果时，独立试算红/黄/绿风险等级（调试分级口径、人工预判交付风险）。独立调用接受显式输入、不读 DB 任务上下文（流水线节点是从库组装同结构输入后调同一函数），不写 risk_classifications 表。

## 独立调用

对应 MCP 工具：`risk_classify`（qvp_mcp v2 能力工具，实现见 `qvp_mcp/tools_capabilities.py`）。

参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| task_id | str | 是 | 配额申请与成本记账键 |
| rule_results | list[dict] | 否 | [{"passed": bool, "rule_name": str}]，即 rule_check 的输出 |
| cross_results | list[dict] | 否 | [{"matched": bool}]，cross_check 的 mismatches 即可 |
| evidence_complete | bool | 否 | P0/P1 关键事实点是否都有支撑证据，默认 true |
| has_p0_issue | bool | 否 | 是否存在未关闭的 P0 问题单，默认 false；为 true 直接判红 |

返回：`{"ok": bool, "data": {...} | null, "error": str | null}`；data 含 `level`（"red" | "yellow" | "green"）、`reasons`（判定理由列表）。

配额：校验类，每任务默认 10 次（settings.mcp_max_check_tools_per_task）；纯函数，成本记账 0。

最小调用示例：

```json
{"task_id": "demo-001", "rule_results": [{"passed": true, "rule_name": "字数"}], "cross_results": [], "evidence_complete": true, "has_p0_issue": false}
```
