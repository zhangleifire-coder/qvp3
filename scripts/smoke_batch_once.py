"""批量灰度驱动：导入 N 条 query → 自动过 text/refs 关卡 → 全终态后输出统计汇总。

用法：PYTHONUTF8=1 .venv/Scripts/python scripts/smoke_batch_once.py
环境：SMOKE_BASE（默认 http://127.0.0.1:8003；服务器 http://117.34.118.115:8005）
"""
import asyncio
import os
import time

import httpx

BASE = os.environ.get("SMOKE_BASE", "http://127.0.0.1:8003")

QUERIES = [
    # general ×10
    ("新生儿保险怎么配置不踩坑", "general"),
    ("出租屋收纳空间翻倍的十个思路", "general"),
    ("空气炸锅和烤箱到底怎么选", "general"),
    ("猫咪换粮软便怎么办", "general"),
    ("颈椎病办公族的工位改造清单", "general"),
    ("洗衣液洗衣凝珠洗衣粉区别", "general"),
    ("新手养绿萝总黄叶的原因", "general"),
    ("地铁通勤双肩包怎么选", "general"),
    ("保温杯材质304和316的区别", "general"),
    ("宝宝辅食添加顺序一览", "general"),
    # single ×5
    ("戴森V12吸尘器真实使用三个月体验", "single"),
    ("小米手环9睡眠监测准不准实测", "single"),
    ("网易严选乳胶枕睡了半年说说感受", "single"),
    ("摩飞便携榨汁杯使用一个月体验", "single"),
    ("公牛轨道插座厨房真实使用分享", "single"),
    # compare ×5
    ("iPhone16和华为Mate70拍照对比", "compare"),
    ("科沃斯T30和石头P10扫地机对比", "compare"),
    ("优衣库和蕉下防晒衣实穿对比", "compare"),
    ("山姆和盒马鲜生会员店购物对比", "compare"),
    ("特斯拉Model3和小米SU7通勤对比", "compare"),
]


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=httpx.Timeout(60.0, read=120.0)) as c:
        r = await c.post("/api/auth/login", json={"username": "张三", "password": "1qaz@WSX"})
        token = r.json().get("token") or r.json().get("access_token")
        auth = {"Authorization": f"Bearer {token}"}

        task_ids = []
        for q, mode in QUERIES:
            r = await c.post("/api/tasks/import_queries", headers=auth,
                             json={"queries": [q], "content_type": "gray0908", "mode": mode})
            ids = r.json().get("task_ids") or []
            task_ids.extend(ids)
        print(f"[import] 导入 {len(task_ids)} 条", flush=True)

        pending = set(task_ids)
        done = {}
        deadline = time.time() + 6 * 3600
        while pending and time.time() < deadline:
            for tid in list(pending):
                try:
                    r = await c.get(f"/api/tasks/{tid}/detail", headers=auth, timeout=30)
                    d = r.json()
                except Exception:
                    continue
                status = (d.get("task") or {}).get("status")
                if status == "awaiting_text":
                    await c.post(f"/api/tasks/{tid}/text/confirm", headers=auth, json={})
                    print(f"[gate] {tid[:8]} text 放行", flush=True)
                elif status == "awaiting_refs":
                    cands = [a["id"] for a in (d.get("assets") or [])
                             if a.get("selection_status") == "candidate"]
                    await c.post(f"/api/tasks/{tid}/refs/confirm", headers=auth,
                                 json={"keep_ids": cands})
                    print(f"[gate] {tid[:8]} refs 放行 keep={len(cands)}", flush=True)
                elif status in ("review", "approved", "failed", "cancelled"):
                    draft = d.get("draft") or {}
                    pages = d.get("page_copies") or []
                    risk = d.get("risk") or {}
                    tl = d.get("node_timeline") or []
                    costs = {n["node"]: n["cost_cny"] for n in tl if n["cost_cny"]}
                    lens = [len(p.get("body") or "") for p in pages]
                    done[tid] = {
                        "status": status, "mode": (d.get("task") or {}).get("mode"),
                        "body_len": len(draft.get("body") or ""),
                        "model": draft.get("model_version"),
                        "pages": lens,
                        "pages_ok": len(lens) == 6 and all(75 <= x <= 135 for x in lens)
                                    and (max(lens) - min(lens)) <= 40 if lens else False,
                        "risk": risk.get("level"),
                        "cost": round(sum(costs.values()), 3),
                        "duration_s": {n["node"]: n["duration_s"] for n in tl},
                    }
                    pending.discard(tid)
                    print(f"[done] {tid[:8]} → {status} risk={risk.get('level')} "
                          f"pages={lens} cost={done[tid]['cost']}", flush=True)
            if pending:
                print(f"[poll {time.strftime('%H:%M:%S')}] 剩余 {len(pending)} 条", flush=True)
                await asyncio.sleep(60)

        print("\n═══ 灰度汇总 ═══")
        print(f"完成 {len(done)} / {len(task_ids)}")
        ok_pages = sum(1 for v in done.values() if v["pages_ok"])
        print(f"分页合规: {ok_pages}/{len(done)}")
        by_risk = {}
        for v in done.values():
            by_risk[v["risk"]] = by_risk.get(v["risk"], 0) + 1
        print(f"风险分布: {by_risk}")
        costs = [v["cost"] for v in done.values()]
        if costs:
            print(f"单条成本: min={min(costs)} max={max(costs)} "
                  f"avg={round(sum(costs)/len(costs), 3)} 总计={round(sum(costs), 2)}")
        durs = [v["duration_s"].get("agent_production") for v in done.values()]
        durs = [x for x in durs if x]
        if durs:
            print(f"agent_production 耗时: min={min(durs)}s max={max(durs)}s "
                  f"avg={round(sum(durs)/len(durs))}s")
        models = {}
        for v in done.values():
            models[v["model"]] = models.get(v["model"], 0) + 1
        print(f"模型分布: {models}")
        fails = [k[:8] for k, v in done.items() if v["status"] == "failed"]
        if fails:
            print(f"失败任务: {fails}")


asyncio.run(main())
