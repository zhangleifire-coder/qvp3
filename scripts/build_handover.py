# 技术交接文档在线版生成器（2026-09-03）
# 把 docs/技术交接文档-*.md（取文件名最新一篇）渲染成 static/handover.html，
# 后端 /handover 路由直接输出它；随发版自动带到服务器，同事浏览器直达。
# 开发机运行（markdown 仅本地依赖，不进 requirements.txt）：
#   .venv/Scripts/python scripts/build_handover.py
import subprocess
from datetime import datetime
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent

DOCS = sorted((ROOT / "docs").glob("技术交接文档-*.md"))
if not DOCS:
    raise SystemExit("docs/ 下找不到 技术交接文档-*.md")
src = DOCS[-1]
raw = src.read_text(encoding="utf-8")

commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                        capture_output=True, text=True).stdout.strip()

body = markdown.markdown(raw, extensions=["tables", "fenced_code", "toc"])

html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>图文生产平台 · 技术交接文档</title>
<style>
  body {{ margin: 0; background: #f6f8fa; color: #1f2328;
         font: 15px/1.8 "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; }}
  .banner {{ background: #1f2328; color: #fff; padding: 14px 24px; font-size: 13px; }}
  .banner b {{ font-size: 15px; }}
  .banner span {{ opacity: .75; margin-left: 12px; }}
  main {{ max-width: 920px; margin: 24px auto 60px; background: #fff;
          padding: 40px 56px; border: 1px solid #d0d7de; border-radius: 8px; }}
  h1 {{ font-size: 26px; border-bottom: 2px solid #d0d7de; padding-bottom: 10px; }}
  h2 {{ font-size: 20px; border-bottom: 1px solid #d0d7de; padding-bottom: 6px;
        margin-top: 34px; }}
  h3 {{ font-size: 16px; margin-top: 24px; }}
  code {{ background: #f0f2f4; border-radius: 4px; padding: 1px 5px;
          font-family: Consolas, monospace; font-size: 13px; }}
  pre {{ background: #f6f8fa; border: 1px solid #d0d7de; border-radius: 6px;
         padding: 12px 14px; overflow-x: auto; line-height: 1.5; }}
  pre code {{ background: none; padding: 0; }}
  table {{ border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 14px; }}
  th, td {{ border: 1px solid #d0d7de; padding: 6px 10px; text-align: left; }}
  th {{ background: #f6f8fa; }}
  tr:nth-child(even) td {{ background: #fbfcfd; }}
  blockquote {{ margin: 0; padding: 4px 16px; color: #59636e;
                border-left: 4px solid #d0d7de; }}
  hr {{ border: none; border-top: 1px solid #d0d7de; margin: 28px 0; }}
  a {{ color: #0969da; }}
</style>
</head>
<body>
<div class="banner"><b>图文生产平台 · 技术交接文档</b>
  <span>源文件 {src.name} ｜ commit {commit or "dev"} ｜ 生成于 {datetime.now():%Y-%m-%d %H:%M}（scripts/build_handover.py，随发版自动更新）</span>
</div>
<main>
{body}
</main>
</body>
</html>
"""

out = ROOT / "static" / "handover.html"
out.write_text(html, encoding="utf-8")
print(f"OK {src.name} -> {out.relative_to(ROOT)}  ({out.stat().st_size} bytes, commit {commit})")
