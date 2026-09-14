"""首过率治理统计（2026-09-14 P2）：按风格统计首轮 OCR 相似度分布与重生率。

数据源：assets.first_sim（024 迁移，garble 质检写入）+ prompt_used 标记
（|regen garble 重生 / |vlappeal VL 申诉放行 / |subjregen 主体重画 / |ai_review）。

用法（本地 8005 库在容器内；开发库用 DATABASE_URL 指 5433）：
  PYTHONUTF8=1 python scripts/garble_stats.py                 # 近 7 天
  PYTHONUTF8=1 python scripts/garble_stats.py --days 30 --min-pages 12
输出：按风格分组的首轮 sim 分布 / 不合格率 / 重生率 / 申诉率排名——
系统性触发重生的风格（不合格率高且申诉率低 = 真渲染问题）做提示词加固；
申诉率高 = OCR 对该版式误读多，申诉通道在起作用，可反推 OCR 弱点。
"""
import argparse
import asyncio
import os

import asyncpg

QUERY = """
SELECT coalesce(t.gen_image_style, '（未指定风格）') AS style,
       t.mode,
       count(*) AS pages,
       round(avg(a.first_sim)::numeric, 3) AS avg_first_sim,
       round(100.0 * count(*) FILTER (WHERE a.first_sim < 1.0) / count(*), 1) AS fail_pct,
       round(100.0 * count(*) FILTER (WHERE a.prompt_used LIKE '%%|regen%%') / count(*), 1) AS regen_pct,
       round(100.0 * count(*) FILTER (WHERE a.prompt_used LIKE '%%vlappeal%%') / count(*), 1) AS appeal_pct,
       round(100.0 * count(*) FILTER (WHERE a.prompt_used LIKE '%%|subj%%') / count(*), 1) AS subj_pct
FROM assets a JOIN tasks t ON t.id = a.task_id
WHERE a.source_type = 'ai_generated' AND a.is_history = false
  AND a.created_at > now() - make_interval(days => $1)
  AND a.first_sim IS NOT NULL
GROUP BY 1, 2
HAVING count(*) >= $2
ORDER BY fail_pct DESC, pages DESC;
"""


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--min-pages", type=int, default=6,
                    help="风格最少页数（默认 6=至少一个任务）")
    args = ap.parse_args()
    url = os.environ.get(
        "DATABASE_URL", "postgresql+asyncpg://qvp:qvp@localhost:5433/qvp")
    dsn = url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(QUERY, args.days, args.min_pages)
    finally:
        await conn.close()
    print(f"近 {args.days} 天首轮 OCR 质检分布（按不合格率排名，≥{args.min_pages} 页）：\n")
    print(f"{'风格':<14}{'模式':<9}{'页数':>4}{'均sim':>8}{'不合格%':>9}"
          f"{'重生%':>7}{'申诉%':>7}{'主体%':>7}")
    for r in rows:
        print(f"{r['style']:<14}{r['mode']:<9}{r['pages']:>4}"
              f"{float(r['avg_first_sim']):>8.3f}{float(r['fail_pct']):>9.1f}"
              f"{float(r['regen_pct']):>7.1f}{float(r['appeal_pct']):>7.1f}"
              f"{float(r['subj_pct']):>7.1f}")
    print("\n读法：不合格%高 + 申诉%低 = 真渲染问题 → 该风格提示词加固；"
          "申诉%高 = OCR 误读多 → 申诉通道生效，不必改提示词。")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
