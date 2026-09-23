"""提取模板导入 compose_templates 表（2026-09-24）。

data/compose_templates_extracted.json（extract_case_templates.py 产出）→
DB 幂等导入：全部入库（source=vl_extracted），popularity >= 阈值（默认 3）
的启用，其余停用待人工预览后启用。seed 模板不碰。

用法：PYTHONUTF8=1 python scripts/import_extracted_templates.py [--min-pop 3]
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SRC = Path(__file__).resolve().parent.parent / "data" / \
    "compose_templates_extracted.json"


async def main(min_pop: int) -> None:
    from sqlalchemy import text
    from src.db.session import SessionLocal
    from src.services.compose_templates import validate_spec

    data = json.loads(SRC.read_text(encoding="utf-8"))
    n_ok = n_en = n_bad = 0
    async with SessionLocal() as session:
        for t in data.get("templates") or []:
            errs = validate_spec(t)
            if errs:
                n_bad += 1
                print(f"[import] 跳过非法 spec {t.get('template_id')}: {errs[:2]}")
                continue
            enabled = int(t.get("popularity", 0)) >= min_pop
            await session.execute(text("""
                INSERT INTO compose_templates
                  (template_id, name, spec, page_role, tags, source,
                   enabled, version, notes)
                VALUES (:tid, :name, CAST(:spec AS jsonb), :role,
                        CAST(:tags AS text[]), 'vl_extracted', :en, 1, :notes)
                ON CONFLICT (template_id) DO UPDATE SET
                  name = EXCLUDED.name, spec = EXCLUDED.spec,
                  page_role = EXCLUDED.page_role, tags = EXCLUDED.tags,
                  enabled = EXCLUDED.enabled, notes = EXCLUDED.notes
            """), {"tid": t["template_id"], "name": t["name"][:60],
                   "spec": json.dumps(t, ensure_ascii=False),
                   "role": t.get("page_role", "content"),
                   "tags": ["提取"] + (t.get("tags") or []),
                   "en": enabled,
                   "notes": str(t.get("notes", ""))[:200]})
            n_ok += 1
            n_en += int(enabled)
        await session.commit()
    print(f"[import] 完成：入库 {n_ok}（启用 {n_en}，阈值 popularity>={min_pop}），"
          f"非法跳过 {n_bad}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-pop", type=int, default=3)
    asyncio.run(main(ap.parse_args().min_pop))
