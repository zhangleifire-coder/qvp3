# 风格自适应选择（直连路径）集成测试（2026-08-31）：
# 1) query 题材命中 → 只在命中风格中加权随机；2) 冷门 query → 全库随机不固定；
# 3) 库空 → 退回代码内置风格库；4) ensure_task_style 任务级锁定幂等（重生成同风格）；
# 5) 风格段注入6页提示词且统一、布局轮换。
# 注：测试库(qvp_test)不种子 style_keywords，本文件自行播种并在用例内清理。
import uuid

import pytest
from sqlalchemy import delete, select

from src.db.session import SessionLocal
from src.models.tasks import Task
from src.models.styles import StyleKeyword
from src.services.style_select import (
    _score, select_image_style, ensure_task_style, build_style_block)
from src.gateway.prompt_versions import get_image_prompt


def _uniq() -> str:
    return uuid.uuid4().hex[:8]


async def _seed(styles: list[tuple[str, str, bool]]):
    """播种风格库（(名, 关键词, 启用)）；先清空本表保证确定性。"""
    async with SessionLocal() as session:
        await session.execute(delete(StyleKeyword))
        for name, kw, en in styles:
            session.add(StyleKeyword(style_name=name, keywords=kw,
                                     description=f"{name}的描述词", enabled=en))
        await session.commit()


def test_score_keyword_hits():
    assert _score("手机对比测评怎么选", "手机,数码,对比,选购") == 2  # 手机+对比
    assert _score("数码手机对比选购", "手机,数码,对比,选购") == 4
    assert _score("量子纠缠科普", "手机,数码") == 0
    assert _score("", "手机") == 0


@pytest.mark.asyncio
async def test_select_returns_matched_styles_only():
    """命中题材：抽样全部落在命中集合内；禁用条目绝不出现。"""
    await _seed([
        ("甲对比卡", "手机,对比,测评,选购", True),
        ("乙对比卡", "对比", True),
        ("丙旅游卡", "旅游,自驾", True),
        ("丁禁用卡", "手机", False),
    ])
    picked = set()
    for _ in range(24):
        name, desc = await select_image_style("手机对比测评选购")
        picked.add(name)
        assert desc
    assert picked <= {"甲对比卡", "乙对比卡"}, picked
    assert "甲对比卡" in picked  # 高分项必然出现


@pytest.mark.asyncio
async def test_select_cold_query_full_library_random():
    """冷门 query：全库等权随机（多样性，不固定默认），禁用条目除外。"""
    await _seed([
        ("甲对比卡", "手机", True),
        ("乙对比卡", "对比", True),
        ("丙旅游卡", "旅游", True),
        ("丁禁用卡", "量子", False),
    ])
    seen = set()
    for _ in range(30):
        name, _ = await select_image_style("量子纠缠原理浅析")
        seen.add(name)
    assert seen <= {"甲对比卡", "乙对比卡", "丙旅游卡"}
    assert len(seen) >= 2  # 三选一的等权随机，30 次只中一种的概率≈3×(1/3)^30≈0


@pytest.mark.asyncio
async def test_select_empty_db_falls_back_to_builtin():
    """库空：退回代码内置 IMAGE_STYLE_LIBRARY（等权随机）。"""
    from src.services.combo import IMAGE_STYLE_LIBRARY
    async with SessionLocal() as session:
        await session.execute(delete(StyleKeyword))
        await session.commit()
    name, desc = await select_image_style("随便什么题材")
    assert name in dict(IMAGE_STYLE_LIBRARY)
    assert desc


@pytest.mark.asyncio
async def test_ensure_task_style_locks_and_persists():
    """任务级锁定：两次调用同名同描述；gen_image_style 落库（重生成沿用）。"""
    await _seed([("甲对比卡", "手机,对比", True)])
    async with SessionLocal() as session:
        task = Task(idempotency_key=f"k-{_uniq()}", query="扫地机器人和洗地机对比",
                    content_type="generic", mode="compare", status="draft")
        session.add(task)
        await session.commit()
        tid = task.id
    try:
        n1, d1 = await ensure_task_style(tid)
        # 已锁定后即使库变了也沿用（幂等，保证重生成同风格）
        await _seed([("丙旅游卡", "旅游", True)])
        n2, d2 = await ensure_task_style(tid)
        assert n1 == n2 == "甲对比卡" and d1 == d2
        async with SessionLocal() as session:
            t = (await session.execute(
                select(Task).where(Task.id == tid))).scalar_one()
            assert t.gen_image_style == "甲对比卡"
    finally:
        async with SessionLocal() as session:
            await session.execute(delete(Task).where(Task.id == tid))
            await session.commit()


@pytest.mark.asyncio
async def test_ensure_task_style_reselcts_when_gone():
    """已锁风格被删出库 → 重选并更新落库（不留死引用）。"""
    async with SessionLocal() as session:
        task = Task(idempotency_key=f"k-{_uniq()}", query="某题材",
                    content_type="generic", mode="general", status="draft",
                    gen_image_style="已删除的风格")
        session.add(task)
        await session.commit()
        tid = task.id
    try:
        await _seed([("丙旅游卡", "旅游,题材", True)])
        n1, _ = await ensure_task_style(tid)
        assert n1 == "丙旅游卡"
        async with SessionLocal() as session:
            t = (await session.execute(
                select(Task).where(Task.id == tid))).scalar_one()
            assert t.gen_image_style == "丙旅游卡"
    finally:
        async with SessionLocal() as session:
            await session.execute(delete(Task).where(Task.id == tid))
            await session.commit()


def test_build_style_block_has_unify_clause():
    b = build_style_block("测试风", "奶油米底")
    assert "本篇视觉风格：测试风" in b and "奶油米底" in b
    assert "同一主色调" in b and "布局" in b


def test_get_image_prompt_style_block_unifies_six_pages():
    """6 页提示词：风格段逐字一致（统一），布局轮换（不重样）。"""
    block = build_style_block("自然写实暖调", "奶油米底写实摄影")
    ps = [get_image_prompt("general", f"第{i}页文案", i, style_block=block)
          for i in range(1, 7)]
    assert all(block in p for p in ps)            # 风格段全篇统一
    tails = {p[p.index("本页是"):] for p in ps}    # 布局各不相同
    assert len(tails) == 6
    assert all("标准中文字体" in p and "30-100 字" in p and "不得使用纯白或纯黑" in p
               for p in ps)  # 字数均衡 + 边框禁纯白纯黑（硬约束底座）
