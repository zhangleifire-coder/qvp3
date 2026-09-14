"""一次性合并：同事 8003 风格库 CSV → 8005 styles.json（SSOT）。

规则（2026-09-14）：
- 同名条目（24 条）：keywords 取并集（CSV 序在前），description 以 CSV（同事 09-14
  最新生产调优版）为准；保留 8005 的 use_when/pitfalls 结构化字段（CSV 无此列）。
- 新条目（6 条）：整行引入，use_when/pitfalls 按描述手工补写（下方 NEW_META），
  source=colleague_8003_20260914。
- 8005 独有条目（实拍质感单品/双主体同框对比/新中式雅集史料卡/6 条 oss_distilled）
  不动。
"""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = Path(__file__).resolve().parent / "_colleague_8003_styles.csv"
JSON_PATH = ROOT / "data" / "styles.json"

# 6 条新风格的 use_when/pitfalls 补写（WS1 纪律：use_when≤40 字；
# pitfalls≤3 条、单条≤30 字、正向优先）
NEW_META = {
    "雾蓝知性卡": {
        "use_when": "知识科普/读书观点/职场法律等知性题材优先",
        "pitfalls": "雾蓝灰与米白双色分区；衬线宋体标题；黛蓝印章点缀页脚",
    },
    "薄荷清新卡": {
        "use_when": "清新生活方式/轻科普/绿植好物题材优先",
        "pitfalls": "薄荷绿与净白双色；圆角卡片分区；胶囊强调薄荷绿底白字",
    },
    "孟菲斯几何撞色卡": {
        "use_when": "年轻潮流/活动创意/新品好物题材优先",
        "pitfalls": "每页颜色不超过四种；几何散点装饰克制；标题可斜切排布",
    },
    "时尚街拍杂志卡": {
        "use_when": "穿搭时尚/美妆探店/街拍好物题材优先",
        "pitfalls": "黑白灰主调一处亮色；标题可跨图排版；短句不超过三条",
    },
    "浅青3D场景卡": {
        "use_when": "科技/职业规划/城市工程科普题材优先",
        "pitfalls": "3D微缩场景哑光柔和；青绿白浅金配色；图标加胶囊条竖排",
    },
    "暗夜对决对比卡": {
        "use_when": "数码/电竞/性能对决类对比题材优先",
        "pitfalls": "上半浅底下半深色地面；红蓝撞色对峙；双方各一句短结论",
    },
}


def main() -> int:
    doc = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    by_name = {s["style_name"]: s for s in doc["styles"]}
    rows = list(csv.DictReader(CSV_PATH.read_text(encoding="utf-8-sig").splitlines()))
    updated = added = 0
    for r in rows:
        name = r["style_name"].strip()
        kw_csv = [k.strip() for k in r["keywords"].split(",") if k.strip()]
        desc = r["description"].strip()
        if name in by_name:
            s = by_name[name]
            merged_kw = list(dict.fromkeys(kw_csv + [k for k in s["keywords"] if k not in kw_csv]))
            s["keywords"] = merged_kw
            s["description"] = desc
            updated += 1
        else:
            meta = NEW_META.get(name, {"use_when": "", "pitfalls": ""})
            doc["styles"].append({
                "style_name": name,
                "keywords": kw_csv,
                "use_when": meta["use_when"],
                "description": desc,
                "pitfalls": meta["pitfalls"],
                "source": "colleague_8003_20260914",
            })
            by_name[name] = doc["styles"][-1]
            added += 1
    JSON_PATH.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    print(f"合并完成：更新 {updated} 条，新增 {added} 条，总计 {len(doc['styles'])} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
