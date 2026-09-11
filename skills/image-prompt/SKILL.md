---
name: image-prompt
description: 配图生成（image_gen）：三模式题材前缀 + 中文硬约束底座 + 英文骨架 + 6 页排版轮换。
notes: 2026-08-31 重构：风格特征从底座移入风格库，底座只留跨风格铁律；2026-09-01 英文视觉骨架（visual_writer 扩写链路）；主体锚定句可被 page_subject 动态替换（subject_anchor.txt 为被替换的通用子串）。layouts_*.txt 内 6 条布局以单独一行 --- 分隔。
---

片段文件：
- `prompts/general.txt`
- `prompts/single.txt`
- `prompts/compare.txt`
- `prompts_en/general.txt`
- `prompts_en/single.txt`
- `prompts_en/compare.txt`
- `shared_style.txt`
- `subject_anchor.txt`
- `constraints_en.txt`
- `layouts_cn.txt`
- `layouts_en.txt`


## 独立调用

对应 MCP 工具：`generate_images`（素材工具，实现见 `qvp_mcp/server.py`）。本包各片段不直接对外暴露——生图提示词由该工具内部组装（三模式题材前缀 + 视觉扩写/中文回退 + 硬约束底座 + 6 页排版轮换 + 主体锚定替换），调用方只传文案与模式等参数。

参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| task_id | str | 是 | 配额申请与成本记账键 |
| pages | list[str] | 是 | 6 页图上文案，顺序即页序 1-6；为空抛 ValueError |
| mode | str | 否 | 生产模式 general / single / compare，默认 general |
| image_template | str | 否 | 后端下发、来自提示词库的生图模板（含 {page_body} 占位），原样传入 |
| reference_urls | list[str] | 否 | compare/single 模式的实景参考图 URL（来自 image_search 结果） |

返回：每页 `{page_index, prompt, image_url（本地路径）, origin_url, hash, size_ok}`；失败页在 warnings 里说明。

配额：生图配额按张计，同任务默认 8 张（含去重重生）。

最小调用示例：

```json
{"task_id": "demo-001", "pages": ["第1页文案", "第2页文案", "第3页文案", "第4页文案", "第5页文案", "第6页文案"]}
```
