"""一次性脚本：对待审核任务用真实 OCR 重新分析（ocr_read → cross_check → risk_classify）。

用法：.venv/bin/python scripts/reanalyze.py
背景：14 条任务是在 OCR 还是桩实现时生产的（全部误判红），真实 OCR 上线后补跑后三个节点。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, delete

from src.db.session import SessionLocal
from src.models.tasks import Task
from src.models.assets import Asset, OcrResult, CrossCheck
from src.models.review import RiskClassification
from src.pipeline.nodes import node_ocr_read, node_cross_check, node_risk_classify


async def reanalyze(task_id):
    async with SessionLocal() as session:
        await session.execute(delete(OcrResult).where(OcrResult.asset_id.in_(
            select(Asset.id).where(Asset.task_id == task_id))))
        await session.execute(delete(CrossCheck).where(CrossCheck.task_id == task_id))
        await session.execute(delete(RiskClassification).where(
            RiskClassification.task_id == task_id))
        await session.commit()
    ocr = await node_ocr_read({"task_id": task_id})
    cc = await node_cross_check({"task_id": task_id})
    risk = await node_risk_classify({"task_id": task_id})
    return ocr, cc, risk


async def main():
    async with SessionLocal() as session:
        tasks = (await session.execute(
            select(Task.id, Task.query, RiskClassification.level)
            .outerjoin(RiskClassification, RiskClassification.task_id == Task.id)
            .where(Task.status == "review"))).all()
    print(f"共 {len(tasks)} 条待审核任务，开始重新分析…")
    for tid, query, old_level in tasks:
        try:
            ocr, cc, risk = await reanalyze(tid)
            print(f"[{old_level} → {risk['level']}] {query[:34]} "
                  f"| 不一致 {cc['mismatch_count']} 项 "
                  f"| 原因 {','.join(risk['reasons']) or '无'} "
                  f"| OCR成本 ¥{ocr.get('cost_cny', 0):.4f}", flush=True)
        except Exception as e:
            print(f"[ERROR] {query[:34]} | {e}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
