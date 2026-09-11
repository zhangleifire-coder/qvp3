"""WS3 风格单一事实来源守卫（2026-09-11）：

① sync 纯函数期望态 == data/styles.json（逐字段、逐序一致）；
② combo.py 代码兜底 IMAGE_STYLE_LIBRARY == json 中 enabled 公共条目；
③ json schema 校验：必备字段齐全、use_when ≤40 字、pitfalls ≤3 条且单条 ≤30 字；
④ sync 幂等 upsert（集成：同名 DO UPDATE；enabled 不被冲掉——管理页临时停用语义）。

纪律：公共库改动一律改 data/styles.json；管理页只管个人库与临时停用。
"""
import json
from pathlib import Path

import pytest
from sqlalchemy import delete, select, text

from src.db.session import SessionLocal
from src.models.styles import StyleKeyword

STYLES_JSON = Path(__file__).resolve().parents[2] / "data" / "styles.json"

from scripts.sync_styles import (  # noqa: E402
    PITFALL_ITEM_MAX, PITFALL_MAX_ITEMS, REQUIRED_FIELDS, USE_WHEN_MAX,
    desired_public_styles, load_styles, sync_public_styles)


def _json_styles() -> list[dict]:
    return json.loads(STYLES_JSON.read_text(encoding="utf-8"))["styles"]


class TestSchemaGuard:
    """③ schema 校验：必备字段/use_when ≤40 字/pitfalls ≤3 条且单条 ≤30 字。"""

    def test_styles_json_valid_and_nonempty(self):
        styles = load_styles()   # 内部即做全量 schema 校验，非法抛 ValueError
        assert len(styles) >= 20   # 2026-09-09 导入后 27 条，防误删兜底下限

    def test_required_fields_and_limits(self):
        for s in _json_styles():
            for f in REQUIRED_FIELDS:
                assert f in s, f"{s.get('style_name')} 缺字段 {f}"
            assert s["style_name"].strip()
            assert isinstance(s["keywords"], list) and s["keywords"]
            assert s["description"].strip()
            assert len(s["use_when"]) <= USE_WHEN_MAX
            import re
            items = [p for p in re.split(r"[；;\n]+", s["pitfalls"] or "") if p.strip()]
            assert len(items) <= PITFALL_MAX_ITEMS
            assert all(len(p) <= PITFALL_ITEM_MAX for p in items)

    def test_schema_violations_rejected(self, tmp_path):
        base = {"style_name": "X", "keywords": ["a"], "use_when": "",
                "description": "d", "pitfalls": "", "source": "manual"}

        def _write(mut):
            s = dict(base)
            mut(s)
            p = tmp_path / "bad.json"
            p.write_text(json.dumps({"version": 1, "styles": [s]},
                                    ensure_ascii=False), encoding="utf-8")
            return p

        with pytest.raises(ValueError):
            load_styles(_write(lambda s: s.pop("pitfalls")))
        with pytest.raises(ValueError):
            load_styles(_write(lambda s: s.update(use_when="长" * 41)))
        with pytest.raises(ValueError):
            load_styles(_write(lambda s: s.update(pitfalls="一；二；三；四")))
        with pytest.raises(ValueError):
            load_styles(_write(lambda s: s.update(pitfalls="超" * 31)))


class TestSyncPureFunction:
    """① sync 纯函数期望态 == json（独立于 load_styles 再解析一遍比对）。"""

    def test_desired_state_matches_json(self):
        rows = desired_public_styles()
        styles = _json_styles()
        assert len(rows) == len(styles)
        for row, s in zip(rows, styles):
            name, keywords, use_when, desc, pitfalls, source = row
            assert name == s["style_name"].strip()
            assert keywords == ",".join(k.strip() for k in s["keywords"])
            assert use_when == s["use_when"].strip()
            assert desc == s["description"].strip()
            assert pitfalls == s["pitfalls"].strip()
            assert source == (s["source"].strip() or "manual")


class TestComboFallback:
    """② combo 代码兜底 == json enabled 公共条目。"""

    def test_image_style_library_matches_json(self):
        from src.services.combo import IMAGE_STYLE_LIBRARY
        expected = [(s["style_name"].strip(), s["description"].strip())
                    for s in _json_styles() if s.get("enabled", True)]
        assert IMAGE_STYLE_LIBRARY == expected

    def test_inline_fallback_when_json_missing(self, tmp_path):
        from src.services.combo import (_FALLBACK_STYLE_LIBRARY,
                                        load_style_entries)
        assert load_style_entries(tmp_path / "nope.json") == []
        assert len(_FALLBACK_STYLE_LIBRARY) == 1   # 内联最小兜底 1 条

    def test_disabled_entries_excluded(self, tmp_path):
        from src.services.combo import load_style_entries
        p = tmp_path / "s.json"
        p.write_text(json.dumps({"version": 1, "styles": [
            {"style_name": "启用", "keywords": ["a"], "use_when": "",
             "description": "d1", "pitfalls": "", "source": "manual"},
            {"style_name": "停用", "keywords": ["b"], "use_when": "",
             "description": "d2", "pitfalls": "", "source": "manual",
             "enabled": False}]}, ensure_ascii=False), encoding="utf-8")
        assert [s["style_name"] for s in load_style_entries(p)] == ["启用"]


class TestSyncDb:
    """④ 幂等 upsert（测试库）：目标态落库；重跑不重复；enabled 不被冲掉。"""

    @pytest.mark.asyncio
    async def test_upsert_idempotent_and_preserves_enabled(self):
        import asyncpg
        import os
        dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://",
                                                 "postgresql://")
        async with SessionLocal() as session:
            await session.execute(delete(StyleKeyword))
            await session.commit()
        conn = await asyncpg.connect(dsn)
        try:
            n1 = await sync_public_styles(conn)
            n2 = await sync_public_styles(conn)   # 幂等重跑
            assert n1 == n2 == len(_json_styles())
            # 管理页临时停用（enabled=false）不被重跑冲掉
            await conn.execute(
                "UPDATE style_keywords SET enabled = FALSE "
                "WHERE owner_id IS NULL AND style_name = $1",
                _json_styles()[0]["style_name"])
            await sync_public_styles(conn)
        finally:
            await conn.close()
        async with SessionLocal() as session:
            rows = list((await session.execute(
                select(StyleKeyword).where(StyleKeyword.owner_id.is_(None)))
            ).scalars().all())
            assert len(rows) == len(_json_styles())
            by_name = {r.style_name: r for r in rows}
            first = by_name[_json_styles()[0]["style_name"]]
            assert first.enabled is False   # 停用状态保留
            assert first.source == "import_20260909"
            for s in _json_styles():
                r = by_name[s["style_name"]]
                assert r.keywords == ",".join(s["keywords"])
                assert (r.use_when or "") == s["use_when"]
                assert (r.pitfalls or "") == s["pitfalls"]
