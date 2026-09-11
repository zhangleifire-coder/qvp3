---
name: text-draft
description: 文字核查起草（text_check）：query 自查 + 正文/分页/生图描述草稿一次产出 JSON。
notes: 三式：check=全新起草（调用方追加 draft-write/shared_persona.txt）；rewrite=手工底稿改写（保留用户事实观点）；feedback=驳回定向修改。模板内 {{ }} 为 .format 转义的字面花括号。
---

片段文件：
- `check.txt`
- `rewrite.txt`
- `feedback.txt`
- `strict_json_suffix.txt`


## 独立调用

对应 MCP 工具：`text_draft`（qvp_mcp v2 能力工具，实现见 `qvp_mcp/tools_capabilities.py`）。与 pipeline/text_check.py「全新起草」分支同一提示词与解析逻辑（含非法 JSON 加强约束重试一次、正文禁词/字数机检 body_rule_issues）。独立调用为纯计算版：不写 tasks.text_review、不改任务状态，草稿只在返回值里，要落库走流水线。

参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| query | str | 是 | 选题全文 |
| task_id | str | 是 | 配额申请与成本记账键 |
| mode | str | 否 | general / single / compare，默认 general |

返回：`{"ok": bool, "data": {...} | null, "error": str | null}`；data 含 `query_clean`（{issues, suggested}）、`body_draft`、`body_issues`、`pages_draft`（≤6 条）、`image_prompt_draft`（≤6 条）、`model`、`auto_ok`、`cost_cny`；模型输出无法解析时 ok=false。

配额：LLM 类，每任务默认 5 次（settings.mcp_max_llm_tools_per_task）。

最小调用示例：

```json
{"query": "选题：新手爸妈如何挑安全座椅", "task_id": "demo-001", "mode": "general"}
```
