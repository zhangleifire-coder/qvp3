---
name: polish
description: 正文两轮校稿（text_check 起草后）：DeepSeek 生文 → Kimi 主校两轮（round1 事实/真人感/字数/标题，round2 终校/小标题/导语）。
notes: 2026-09-07 设计变更「DeepSeek 生文，Kimi 两轮校稿修正」；片段含 {body} 占位（待校正文）。运行时经 get_effective_prompt("polish_round1/2") 三级覆盖取词（用户自定义→admin→本包默认）；Kimi 主、DeepSeek 备，单轮失败/返回空/校后不足校前 60% 弃用该轮保留前文，不阻断流水线。
---

片段文件：
- `round1.txt`（第一轮校稿检查单）
- `round2.txt`（第二轮终校检查单）

## 运行时接入

`src/pipeline/text_check.py` 起草 JSON 解析成功后、落库前对 `body_draft` 依次执行两轮校稿（仅校正文，pages/image_prompts 不动），校稿成本计入 text_check 节点成本，留痕存 text_review.polish。
