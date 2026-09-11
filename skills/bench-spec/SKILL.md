---
name: bench-spec
description: 标杆交付规范（bench_{mode}）：成功案例共性提炼，注入 Agent 创作指令。
notes: DB 优先：prompt_templates 表 stage='bench_general'/'bench_single'/'bench_compare'（owner_id IS NULL AND is_active，analyze_benchmark.py 产出）；DB 无行时回退本包 default_{mode}.txt 默认基线（2026-09-03 新增兜底，此前注入空串）。
---

片段文件：
- `default_general.txt`
- `default_single.txt`
- `default_compare.txt`


## 独立调用

暂无独立工具：本包是标杆交付规范（成功案例共性提炼），经 Agent 创作指令注入生效（DB prompt_templates 表 bench_{mode} 行优先，本包 default_{mode}.txt 兜底），没有对应的 MCP 独立调用工具。
