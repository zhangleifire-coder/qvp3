---
name: cross-check
description: OCR 关键字段对撞（cross_check）独立调用：分页文案关键数字/年份/型号 vs 各页配图 OCR 文字，逐页比对；确定性代码，不调 LLM。
notes: 2026-09-03 qvp_mcp v2 新增；与流水线 cross_check 节点共用同一实现（quality/cross_check.extract_key_fields + compare_field）。
---

功能说明：独立复核「图上文字与分页文案一致性」：从各页文案提取关键字段（数字/年份/型号等），与对应页配图 OCR 文字逐页对撞，输出不一致清单。独立调用不写 cross_checks 表。

## 独立调用

对应 MCP 工具：`cross_check`（qvp_mcp v2 能力工具，实现见 `qvp_mcp/tools_capabilities.py`）。

参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| pages | list[str] | 是 | 各页文案（顺序即页序 1..n） |
| ocr_texts | list[str] | 是 | 与 pages 等长的各页 OCR 原始文字；空串/缺失=该页识别失败，记一条 ocr mismatch（同节点 confidence=0 口径） |
| task_id | str | 是 | 配额申请与成本记账键 |

返回：`{"ok": bool, "data": {...} | null, "error": str | null}`；data 含 `mismatches`（[{"field_name", "expected", "actual", "matched"}]）、`mismatch_count`（int）。

配额：校验类，每任务默认 10 次（settings.mcp_max_check_tools_per_task）；不调 LLM，成本记账 0。

最小调用示例：

```json
{"pages": ["第1页文案……", "第2页文案……"], "ocr_texts": ["第1图OCR文字……", "第2图OCR文字……"], "task_id": "demo-001"}
```
