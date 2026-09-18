# visual-writer 独立调用

> 本包 SKILL.md 正文即 LLM 提示词模板（src/gateway/skill_loader.skill_body 原样读取后直接喂模型），为避免文档混入提示词，独立调用说明单独放在本文件。

对应 MCP 工具：`visual_write`（qvp_mcp v2 能力工具，实现见 `qvp_mcp/tools_capabilities.py`）。与 services/visual_writer 同一实现：dsh 固定记忆会话优先（跨任务沉淀风格取向与审图反馈笔记），90s 超时/失败回退 DeepSeek/Kimi failover。

参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| pages | list[str] | 是 | 6 页中文文案（顺序即页序，不足 6 条自动补空） |
| task_id | str | 是 | 配额申请与成本记账键 |
| style_name | str | 否 | 本篇视觉风格名（来自风格库） |
| style_desc | str | 否 | 本篇视觉风格描述（来自风格库） |
| notes | list[str] | 否 | 审图反馈笔记，注入记忆会话上下文 |

返回：`{"ok": bool, "data": {...} | null, "error": str | null}`；data 含 `visuals`（{"style_en", "pages": [6 条英文描述]} 或 null）、`fallback_to_cn_skeleton`（bool）。visuals=null 表示两级扩写均未产出可解析结果（流水线语义=回退中文骨架生图，不算调用失败）。成本：内部经记忆会话/回退调用，usage 不上抛，本工具记账 0。

配额：LLM 类，每任务默认 5 次（settings.mcp_max_llm_tools_per_task）。

最小调用示例：

```json
{"pages": ["第1页文案", "第2页文案", "第3页文案", "第4页文案", "第5页文案", "第6页文案"], "task_id": "demo-001"}
```
