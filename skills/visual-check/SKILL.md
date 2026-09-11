---
name: visual-check
description: VL 图文一致性审核（visual_check）独立调用：qwen-vl 判定图中主体是什么、是否与该页文案主题一致。
notes: 2026-09-03 qvp_mcp v2 新增；与 services/visual_check.check_subject_match 同一实现（dashscope qwen-vl，复用 OCR 网关）。
---

功能说明：独立复核某张配图与某页文案的主体一致性——看图回答图中主体是什么、是否与该页文案主题一致。verdict=null 表示 VL 审核未启用或调用/解析失败（流水线语义=跳过不阻塞，人工关卡兜底）。

## 独立调用

对应 MCP 工具：`visual_check`（qvp_mcp v2 能力工具，实现见 `qvp_mcp/tools_capabilities.py`）。

参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| image_url | str | 是 | 图片地址：generate_images 返回的本地路径（/static/generated/...）或 http(s) URL |
| page_copy | str | 是 | 该页文案 |
| task_id | str | 是 | 配额申请与成本记账键 |

返回：`{"ok": bool, "data": {...} | null, "error": str | null}`；data 含 `verdict`（{"ok": bool, "actual": 图中主体} 或 null）、`skipped`（bool，verdict 为 null 时为 true）。

配额：校验类，每任务默认 10 次（settings.mcp_max_check_tools_per_task）；成本记账 0。

最小调用示例：

```json
{"image_url": "/static/generated/demo_p1.png", "page_copy": "第1页文案……", "task_id": "demo-001"}
```
