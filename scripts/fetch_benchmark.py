"""批量抓取标杆案例（newrank 预览页，CDP 渲染）→ benchmark_cases 表 + 截图。

用法：PYTHONUTF8=1 python scripts/fetch_benchmark.py <案例.xlsx> [--limit N]
- 三列：A=general / D=single / G=compare
- 每条：标题+全文（innerText）、轮播逐页图 URL（点下一页）、首页截图存
  static/benchmark/{id}.jpg（对标审核用——直链有防盗链/过期风险）
"""
import asyncio
import base64
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

import asyncpg
import openpyxl
import websockets

CODE = "C:/Users/Zhang/iCloudDrive/Documents/安居-图文/2026-8-21-分支开发/图文生产平台_移交包_20260822/code"
PORT = 9241
COLS = {"general": "A", "single": "D", "compare": "G"}


def load_links(xlsx: str):
    wb = openpyxl.load_workbook(xlsx, data_only=True)
    ws = wb["Sheet1"]
    out = []
    for mode, col in COLS.items():
        for r in range(2, ws.max_row + 1):
            v = ws[f"{col}{r}"].value
            if v and str(v).startswith("http"):
                out.append((mode, str(v).strip()))
    return out


async def fetch_one(ws, mid_ref, url: str) -> dict:
    """CDP 抓一条：导航→等渲染→取文本→翻轮播收图→首页截图。"""
    async def call(method, params=None, timeout=20):
        mid_ref[0] += 1
        my = mid_ref[0]
        await ws.send(json.dumps({"id": my, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=4))
            if msg.get("id") == my:
                return msg
        return None

    await call("Page.navigate", {"url": url})
    await asyncio.sleep(7)   # 等 JS 渲染
    r = (await call("Runtime.evaluate", {"expression":
        "JSON.stringify({"
        " title: (document.querySelector('[class*=_title_]')||{}).innerText || document.title,"
        " text: document.body.innerText,"
        " pages: (document.querySelector('[class*=_pageIndicator_]')||{innerText:'0'}).innerText.trim()"
        "})", "returnByValue": True}))
    if not r:
        return {}
    d = json.loads(r["result"]["result"]["value"])
    pages = 0
    for ch in (d.get("pages") or "0").split("/"):
        if ch.strip().isdigit():
            pages = max(pages, int(ch.strip()))
    # 首页截图（对标样例）
    shot = await call("Page.captureScreenshot", {"format": "jpeg", "quality": 70})
    shot_b64 = shot["result"]["data"] if shot else None
    # 翻轮播逐页取图
    imgs = []
    for p in range(max(pages, 1)):
        ri = (await call("Runtime.evaluate", {"expression":
            "Array.from(document.querySelectorAll('[class*=imageWrapper] img, [class*=carousel] img'))"
            ".map(i => i.src).filter(s => s && !s.startsWith('data:')).slice(-1)[0] || ''",
            "returnByValue": True}))
        u = ri["result"]["result"]["value"] if ri else ""
        if u and u not in imgs:
            imgs.append(u)
        if p < pages - 1:
            await call("Runtime.evaluate", {"expression":
                "(document.querySelector('[class*=_nextButton_]')||{}).click && "
                "document.querySelector('[class*=_nextButton_]').click(); 1"})
            await asyncio.sleep(1.2)
    return {"title": (d.get("title") or "")[:200],
            "text": (d.get("text") or "")[:4000], "pages": pages,
            "imgs": imgs[:12], "shot": shot_b64}


async def main():
    xlsx = sys.argv[1]
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 0
    links = load_links(xlsx)
    if limit:
        links = links[:limit]
    print(f"待抓取 {len(links)} 条", flush=True)

    shot_dir = Path(CODE) / "static" / "benchmark"
    shot_dir.mkdir(parents=True, exist_ok=True)
    conn = await asyncpg.connect("postgresql://qvp:qvp@localhost:5433/qvp")
    await conn.execute(open(Path(CODE) / "migrations" / "013_benchmark_cases.sql",
                             encoding="utf-8").read())

    prof = Path(tempfile.mkdtemp(prefix="chrome-bm-"))
    proc = subprocess.Popen([
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "--headless", "--disable-gpu", f"--remote-debugging-port={PORT}",
        f"--user-data-dir={prof}", "--no-first-run", "about:blank"])
    time.sleep(4)
    tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
    page = next(t for t in tabs if t.get("type") == "page")
    ok = fail = dup = 0
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=10**7) as ws:
        await asyncio.to_thread(lambda: None)  # noop
        mid_ref = [0]
        await ws.send(json.dumps({"id": 900000, "method": "Runtime.enable"}))
        await asyncio.sleep(1)
        for i, (mode, url) in enumerate(links, 1):
            try:
                exists = await conn.fetchval(
                    "SELECT id FROM benchmark_cases WHERE source_url = $1", url)
                if exists:
                    dup += 1
                    continue
                d = await fetch_one(ws, mid_ref, url)
                if not d or not d["text"] or len(d["text"]) < 80:
                    fail += 1
                    print(f"[{i}] 空内容跳过: {url[:60]}", flush=True)
                    continue
                cid = uuid.uuid4()
                shot_path = None
                if d["shot"]:
                    shot_path = f"/static/benchmark/{cid.hex[:12]}.jpg"
                    (shot_dir / f"{cid.hex[:12]}.jpg").write_bytes(
                        base64.b64decode(d["shot"]))
                await conn.execute(
                    "INSERT INTO benchmark_cases (id, mode, source_url, title, body_text, "
                    "page_count, image_urls, shot_path) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
                    cid, mode, url, d["title"], d["text"], d["pages"] or 0,
                    json.dumps(d["imgs"], ensure_ascii=False), shot_path)
                ok += 1
                print(f"[{i}/{len(links)}] ✓ {mode} {d['title'][:30]} "
                      f"({d['pages']}页 {len(d['imgs'])}图)", flush=True)
            except Exception as e:  # noqa: BLE001
                fail += 1
                print(f"[{i}] ✗ {type(e).__name__}: {str(e)[:100]}", flush=True)
    proc.terminate()
    import shutil
    shutil.rmtree(prof, ignore_errors=True)
    await conn.close()
    print(f"=== 完成: 成功 {ok} | 跳过(已有) {dup} | 失败 {fail}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
