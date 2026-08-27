"""标杆案例共性分析（B）：统计 + LLM 提炼 → 落地提示词/风格库 + 报告。

产出：
1. docs/标杆案例分析报告.md —— 三类案例统计与提炼规范
2. prompt_templates 系统级条目 stage='bench_general'/'bench_single'/'bench_compare'
   （agent_production 读取注入【标杆交付规范】段落）
3. style_keywords 批量导入（从案例提炼的视觉风格 → 生成时自动匹配）

用法：PYTHONUTF8=1 python scripts/analyze_benchmark.py
"""
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import asyncpg

DSN = "postgresql://qvp:qvp@localhost:5433/qvp"

_ANALYZE_PROMPT = """你是内容交付质量分析师。下面是 {mode} 类型小红书图文的 {n} 条成功交付案例全文（标题+正文）。
分析共性并提炼「交付规范」，只输出 JSON：

{samples}

输出 JSON 结构（全部中文，务实可执行，不要空话）：
{{
  "title_patterns": ["封面/标题钩子的 2-3 种主流写法（各附一个案例中的实例）"],
  "body_structure": ["正文信息组织规律（开头怎么起/中段分几个信息块/结尾怎么收）"],
  "tone": "文案口吻一句话概括",
  "page_copy_rules": ["图上文案的写法规律（每页信息量/句式/数字用法）"],
  "image_style": "配图视觉风格共性一句话（构图/色调/质感）",
  "dos": ["3-5 条必须做"],
  "donts": ["3-5 条必须避免"]
}}"""


def classify_title(t: str) -> str:
    if re.match(r"^\d", t):
        return "数字式"
    if "?" in t or "？" in t or re.search(r"(怎么|如何|为什么|哪些|多少)", t):
        return "疑问式"
    if "！" in t or re.search(r"(千万别|必看|避雷|警惕)", t):
        return "警示式"
    return "陈述式"


async def stats(conn, mode: str, rows) -> dict:
    pages = [r["page_count"] or 0 for r in rows]
    bodies = [r["body_text"] or "" for r in rows]
    body_lens = [len(re.sub(r"\s", "", b)) for b in bodies]
    titles = [r["title"] or "" for r in rows]
    title_styles = {}
    for t in titles:
        k = classify_title(t)
        title_styles[k] = title_styles.get(k, 0) + 1
    # 小标题模式：一、/1./短句竖排等
    has_cn_num = sum(1 for b in bodies if re.search(r"[一二三四五六]、", b))
    has_digit = sum(1 for b in bodies if re.search(r"\n\s*\d[\.、]", b))
    return {
        "count": len(rows),
        "pages_avg": round(sum(pages) / max(1, len(pages)), 1),
        "pages_dist": {str(p): pages.count(p) for p in sorted(set(pages)) if p},
        "body_len_avg": round(sum(body_lens) / max(1, len(body_lens))),
        "body_len_range": (min(body_lens), max(body_lens)) if body_lens else (0, 0),
        "title_len_avg": round(sum(len(t) for t in titles) / max(1, len(titles))),
        "title_styles": title_styles,
        "cn_num_sections": has_cn_num,
        "digit_sections": has_digit,
    }


async def llm_insight(mode: str, rows) -> dict:
    from src.gateway.failover import call_with_failover
    samples = "\n\n---\n\n".join(
        f"【标题】{r['title'] or '(无)'}\n{r['body_text'][:800]}" for r in rows[:10])
    prompt = _ANALYZE_PROMPT.format(mode=mode, n=min(10, len(rows)), samples=samples)
    result = await call_with_failover(prompt)
    raw = (result["text"] or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json").strip()
    try:
        return json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
    except Exception:  # noqa: BLE001
        return {"error": raw[:200]}


def insight_to_section(mode: str, st: dict, ins: dict) -> str:
    """提炼结果 → agent 指令注入段落。"""
    lines = [f"【{mode} 标杆交付规范（源自 {st['count']} 条成功案例共性提炼）】"]
    for p in (ins.get("title_patterns") or [])[:3]:
        lines.append(f"- 标题：{p}")
    for b in (ins.get("body_structure") or [])[:3]:
        lines.append(f"- 结构：{b}")
    if ins.get("tone"):
        lines.append(f"- 口吻：{ins['tone']}")
    for r in (ins.get("page_copy_rules") or [])[:3]:
        lines.append(f"- 图上文案：{r}")
    if ins.get("image_style"):
        lines.append(f"- 配图：{ins['image_style']}")
    dos = "；".join(ins.get("dos") or [])[:200]
    donts = "；".join(ins.get("donts") or [])[:200]
    if dos:
        lines.append(f"- 必须做：{dos}")
    if donts:
        lines.append(f"- 必须避免：{donts}")
    lines.append(f"- 统计基准：平均 {st['pages_avg']} 页图、正文约 {st['body_len_avg']} 字、"
                 f"标题约 {st['title_len_avg']} 字（标题风格分布 {st['title_styles']}）")
    return "\n".join(lines)


STYLE_FROM_INSIGHT = {
    # 从案例提炼的共性视觉风格 → 风格关键词库（生成时 Agent 自动匹配）
    "single": ("实拍质感单品", "产品,实测,单品,开箱,评测,推荐,买",
               "真实产品实拍质感、自然光、浅景深特写、生活化场景、干净背景"),
    "compare": ("双主体同框对比", "对比,评测,哪个好,区别,VS,和,还是,选",
                "两个主体左右同框对比构图、参数并排清晰、对比色块标注、公平呈现"),
    "general": ("治愈生活插画", "怎么选,推荐,攻略,指南,科普,避免,坑",
                "柔和暖色调、圆润造型、生活场景插画、留白呼吸感、信息分块清晰"),
}


async def main():
    conn = await asyncpg.connect(DSN)
    rows = await conn.fetch("SELECT mode, title, body_text, page_count "
                            "FROM benchmark_cases ORDER BY fetched_at")
    by_mode = {}
    for r in rows:
        by_mode.setdefault(r["mode"], []).append(r)
    print(f"案例总数 {len(rows)}：", {k: len(v) for k, v in by_mode.items()}, flush=True)

    report = ["# 标杆案例分析报告（2026-08-28）", "",
              f"数据源：{len(rows)} 条成功交付案例"
              f"（general {len(by_mode.get('general', []))} / "
              f"single {len(by_mode.get('single', []))} / "
              f"compare {len(by_mode.get('compare', []))}）", ""]
    for mode in ("general", "single", "compare"):
        rows_m = by_mode.get(mode) or []
        if not rows_m:
            continue
        st = await stats(conn, mode, rows_m)
        ins = await llm_insight(mode, rows_m)
        print(f"=== {mode}: 统计完成，LLM 提炼{'OK' if 'error' not in ins else '失败'}", flush=True)
        report += [f"## {mode}（{st['count']} 条）", "",
                   f"- 页数：平均 {st['pages_avg']}，分布 {st['pages_dist']}",
                   f"- 正文：平均 {st['body_len_avg']} 字（{st['body_len_range'][0]}~{st['body_len_range'][1]}）",
                   f"- 标题：平均 {st['title_len_avg']} 字，风格 {st['title_styles']}",
                   f"- 分节：中文序号（一、二…）{st['cn_num_sections']} 条 / 数字序号 {st['digit_sections']} 条", ""]
        for k, v in ins.items():
            if k == "error":
                report.append(f"> LLM 提炼失败：{v}")
            else:
                report.append(f"- **{k}**：{v}")
        report.append("")
        # 1) 落 prompt_templates：stage='bench_'+mode（系统级，agent 注入）
        section = insight_to_section(mode, st, ins)
        await conn.execute("DELETE FROM prompt_templates WHERE stage = $1 AND owner_id IS NULL",
                           f"bench_{mode}")
        await conn.execute(
            """INSERT INTO prompt_templates (stage, mode, owner_id, name, content, is_active)
               VALUES ($1, NULL, NULL, $2, $3, TRUE)""",
            f"bench_{mode}", f"{mode} 标杆交付规范（自动提炼）", section)
    # 2) 风格关键词库导入
    for mode, (name, kws, desc) in STYLE_FROM_INSIGHT.items():
        await conn.execute(
            """INSERT INTO style_keywords (style_name, keywords, description)
               VALUES ($1, $2, $3)
               ON CONFLICT (style_name) DO UPDATE SET
                 keywords = EXCLUDED.keywords, description = EXCLUDED.description,
                 updated_at = now()""",
            name, kws, desc)
        print(f"风格库: {name} ✓", flush=True)
    await conn.close()
    out = Path(__file__).resolve().parent.parent / "docs" / "标杆案例分析报告.md"
    out.write_text("\n".join(report), encoding="utf-8")
    print(f"报告: {out}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
