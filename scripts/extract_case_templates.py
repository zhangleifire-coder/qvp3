"""0922 案例图批量提取页型模板（2026-09-24 P3 落地）。

对 cases/（30 套 × 5 页 = 150 图）逐图：
1. VL 版式分析（封闭词汇表）→ analysis；
2. 确定性映射 → spec 草稿（vl_extracted）；
3. spec_signature 去重聚类（首个为代表模板，统计 popularity）；
4. 每个代表模板用「案例原图当占位插画」渲染预览 PNG + 拼版总览。

输出：
- data/compose_templates_extracted.json（模板 + 每套每页分析语料）
- compare_extract/previews/*.png + contact_sheet.png

用法：PYTHONUTF8=1 python scripts/extract_case_templates.py [--limit N] [--resume]
"""
import argparse
import asyncio
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CASES_DIR = Path(__file__).resolve().parents[2] / "cases"
OUT_JSON = Path(__file__).resolve().parent.parent / "data" / "compose_templates_extracted.json"
PREVIEW_DIR = Path(__file__).resolve().parents[2] / "compare_extract" / "previews"
CKPT = Path(__file__).resolve().parents[2] / "compare_extract" / "analyses.json"

_SAMPLE = {
    "section_title": "冷感耳环：通勤主力", "section_no": 2,
    "paragraph": ("上班戴得多的是冷感耳环。银色、枪色、哑光钛钢用线条和"
                  "几何撑气质，直径2到3厘米小圆环，单只别超5克。"),
    "points": ["直径2-3厘米", "单只不超5克"], "subtitle": "通勤优先",
    "sticker_text": "通勤",
}


def _data_url(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(
        path.read_bytes()).decode()


async def main(limit: int, resume: bool) -> None:
    from src.services.template_extract import analyze_layout, analysis_to_spec
    from src.services.compose_templates import spec_signature

    files = []
    for n in range(30):
        for p in range(1, 6):
            f = CASES_DIR / f"n{n:02d}_p{p}.png"
            if f.exists():
                files.append((n, p, f))
    if limit:
        files = files[:limit]
    print(f"[extract] 待分析 {len(files)} 图")

    analyses: dict[str, dict] = {}
    if resume and CKPT.exists():
        analyses = json.loads(CKPT.read_text(encoding="utf-8"))
        print(f"[extract] 断点续跑：已有 {len(analyses)} 条")

    sem = asyncio.Semaphore(3)

    async def one(n: int, p: int, f: Path):
        key = f.name
        if key in analyses:
            return
        async with sem:
            a = await analyze_layout(_data_url(f))
        analyses[key] = a or {"error": True}
        ok = "ok" if a else "FAIL"
        print(f"[extract] n{n:02d}_p{p} {ok}"
              + (f" {a.get('arrangement')}/{a.get('page_role')}" if a else ""))

    await asyncio.gather(*[one(n, p, f) for n, p, f in files])
    CKPT.parent.mkdir(parents=True, exist_ok=True)
    CKPT.write_text(json.dumps(analyses, ensure_ascii=False, indent=1),
                    encoding="utf-8")

    # 去重聚类 → 代表模板
    clusters: dict[str, dict] = {}
    corpus = []
    for (n, p, f) in files:
        a = analyses.get(f.name)
        if not a or a.get("error"):
            continue
        corpus.append({"set": n, "page": p, "file": f.name,
                       "query_idx": n, "analysis": a})
        tid = f"case_{n:02d}_p{p}"
        spec, errs = analysis_to_spec(a, tid, f"案例提取 n{n:02d}p{p}")
        if errs:
            continue
        sig = spec_signature(spec)
        c = clusters.setdefault(sig, {
            "spec": {**spec, "template_id": f"ext_{len(clusters)+1:02d}",
                     "name": ""},
            "members": [], "example": f.name, "example_analysis": a})
        c["members"].append(f.name)
    # 代表模板命名：按结构摘要
    for i, c in enumerate(clusters.values(), 1):
        s = c["spec"]
        ph = s["photo"]
        arr_cn = {"single": "单图", "side_by_side": "双图对比",
                  "grid_2x2": "四宫格", "triple_row": "三联",
                  "one_big_two_small": "一大两小", "none": "纯排版"}.get(
            ph["arrangement"], ph["arrangement"])
        s["name"] = (f"提取·{arr_cn}{int(ph.get('top_fraction', 0.57)*100)}%"
                     f"·{s['page_role']}·x{len(c['members'])}")
        c["spec"]["template_id"] = f"ext_{i:02d}"

    # 渲染预览（案例原图当占位插画）
    PREVIEW_DIR.parent.mkdir(parents=True, exist_ok=True)
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    from src.services.compose_renderer import render_card
    previews = []
    for c in clusters.values():
        s = c["spec"]
        if s["photo"]["arrangement"] == "none":
            ills = []
        else:
            ills = [CASES_DIR / c["example"]]
        content = dict(_SAMPLE, title=(c["example_analysis"].get(
            "title_text") or "示例标题")[:18])
        try:
            out = render_card(s, content, ills, style_desc="",
                              out_path=PREVIEW_DIR /
                              f"{s['template_id']}.png")
            previews.append({"template_id": s["template_id"],
                             "name": s["name"], "preview": str(out),
                             "popularity": len(c["members"]),
                             "members": c["members"]})
        except Exception as e:  # noqa: BLE001
            print(f"[preview] {s['template_id']} 失败: {e}")

    # 拼版总览
    if previews:
        from PIL import Image, ImageDraw
        cols = 5
        rows = (len(previews) + cols - 1) // cols
        cw, ch = 240, 400
        sheet = Image.new("RGB", (cols * cw, rows * ch), (245, 242, 236))
        dr = ImageDraw.Draw(sheet)
        for i, pv in enumerate(previews):
            x, y = (i % cols) * cw, (i // cols) * ch
            im = Image.open(pv["preview"]).resize((cw - 12, 320))
            sheet.paste(im, (x + 6, y + 6))
            dr.text((x + 8, y + 332), pv["name"][:22], fill=(40, 38, 46))
        sheet.save(PREVIEW_DIR.parent / "contact_sheet.png")

    OUT_JSON.write_text(json.dumps({
        "version": 1,
        "templates": [{**c["spec"], "popularity": len(c["members"]),
                       "members": c["members"],
                       "notes": (f"代表图：{c['example']}｜标题转录："
                                 f"{str(c['example_analysis'].get('title_text', ''))[:20]}")}
                      for c in clusters.values()],
        "corpus": corpus,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[extract] 完成：{len(clusters)} 个去重模板，语料 {len(corpus)} 页，"
          f"预览 {len(previews)} 张 → {PREVIEW_DIR}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()
    asyncio.run(main(args.limit, args.resume))
