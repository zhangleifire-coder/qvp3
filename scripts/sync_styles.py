"""公共风格库同步（WS3 单一事实来源，2026-09-11）。

纪律：公共库（owner_id IS NULL）改动一律改 data/styles.json 后跑本脚本
（init_db 启动时自动调用）；管理页只管个人库与公共条目的临时停用
（enabled=false——sync 的 ON CONFLICT DO UPDATE 故意不触碰 enabled，
重跑同步不会冲掉管理页的停用状态，否则这是本方案最大的坑）。

schema（data/styles.json）：
  version: int
  styles: [{style_name, keywords[list[str]], use_when, description,
            pitfalls, source, enabled?}]
  约束（tests/unit/test_style_ssot.py 守卫）：use_when ≤40 字；
  pitfalls ≤3 条（；/;/换行分隔）、单条 ≤30 字（措辞纪律：正向优先）。
"""
import json
import os
from pathlib import Path

STYLES_JSON = Path(__file__).resolve().parent.parent / "data" / "styles.json"

REQUIRED_FIELDS = ("style_name", "keywords", "use_when", "description",
                   "pitfalls", "source")
USE_WHEN_MAX = 40
PITFALL_MAX_ITEMS = 3
PITFALL_ITEM_MAX = 30


def load_styles(path: Path | None = None) -> list[dict]:
    """读 styles.json 并做 schema 校验；非法即抛 ValueError（同步宁可失败不放行）。"""
    data = json.loads((path or STYLES_JSON).read_text(encoding="utf-8"))
    styles = data.get("styles")
    if not isinstance(styles, list):
        raise ValueError("styles.json 缺少 styles 数组")
    for i, s in enumerate(styles):
        where = f"styles[{i}]({s.get('style_name', '?')})"
        for f_ in REQUIRED_FIELDS:
            if f_ not in s:
                raise ValueError(f"{where} 缺必备字段 {f_}")
        if not str(s["style_name"]).strip():
            raise ValueError(f"styles[{i}] style_name 为空")
        if not isinstance(s["keywords"], list):
            raise ValueError(f"{where} keywords 必须是数组")
        if len(str(s["use_when"])) > USE_WHEN_MAX:
            raise ValueError(f"{where} use_when 超过 {USE_WHEN_MAX} 字")
        items = [p for p in _pitfall_items(str(s["pitfalls"]))]
        if len(items) > PITFALL_MAX_ITEMS:
            raise ValueError(f"{where} pitfalls 超过 {PITFALL_MAX_ITEMS} 条")
        for p in items:
            if len(p) > PITFALL_ITEM_MAX:
                raise ValueError(f"{where} pitfalls 单条超过 {PITFALL_ITEM_MAX} 字：{p}")
    return styles


def _pitfall_items(pitfalls: str) -> list[str]:
    """pitfalls 文本 → 条目列表（；/;/换行分隔，去空）。"""
    import re
    return [p.strip() for p in re.split(r"[；;\n]+", pitfalls or "") if p.strip()]


def desired_public_styles(path: Path | None = None) -> list[tuple]:
    """sync 纯函数：json → 期望的公共库目标态（幂等 upsert 的参数行）。

    行序保持 json 顺序；enabled 不在目标态内（见模块 docstring 纪律）。
    """
    return [
        (s["style_name"].strip(),
         ",".join(k.strip() for k in s["keywords"] if str(k).strip()),
         str(s["use_when"]).strip(),
         str(s["description"]).strip(),
         str(s["pitfalls"]).strip(),
         str(s["source"]).strip() or "manual")
        for s in load_styles(path)
    ]


_UPSERT_SQL = """
INSERT INTO style_keywords
    (owner_id, style_name, keywords, use_when, description, pitfalls, source, enabled)
VALUES (NULL, $1, $2, $3, $4, $5, $6, TRUE)
ON CONFLICT (style_name) WHERE owner_id IS NULL
DO UPDATE SET keywords    = EXCLUDED.keywords,
              use_when    = EXCLUDED.use_when,
              description = EXCLUDED.description,
              pitfalls    = EXCLUDED.pitfalls,
              source      = EXCLUDED.source,
              updated_at  = now()
"""


async def sync_public_styles(conn, path: Path | None = None) -> int:
    """把 json 期望态幂等 upsert 进公共库；返回同步条数。conn 为 asyncpg 连接。"""
    rows = desired_public_styles(path)
    for row in rows:
        await conn.execute(_UPSERT_SQL, *row)
    return len(rows)


async def _main() -> None:
    import asyncpg
    url = os.environ.get(
        "DATABASE_URL", "postgresql+asyncpg://qvp:qvp@localhost:5433/qvp")
    dsn = url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        n = await sync_public_styles(conn)
        print(f"[sync_styles] 公共风格库已同步 {n} 条（来源 data/styles.json）")
    finally:
        await conn.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(_main())
