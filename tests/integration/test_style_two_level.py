# 两级风格库+偏好闭环+分页画面主体（2026-08-31 移植 qvp-dev 8002）集成测试：
# 作用域（个人/公共）、权限（401/403/404）、钉选直通、stats 聚合、page_subject
# 提取与提示词注入。测试库 qvp_test 内自建用户，用例内清理。
import uuid

import pytest
from sqlalchemy import delete, select, text

from src.db.session import SessionLocal
from src.models.styles import StyleKeyword
from src.models.tasks import Task
from src.models.assets import Asset


def _uniq() -> str:
    return uuid.uuid4().hex[:8]


async def _mk_user(name: str, role: str = "A") -> uuid.UUID:
    async with SessionLocal() as session:
        uid = uuid.uuid4()
        await session.execute(text(
            "INSERT INTO users (id, name, role, active) "
            "VALUES (:i, :n, :r, true) ON CONFLICT DO NOTHING"),
            {"i": str(uid), "n": name, "r": role})
        await session.commit()
        # 处理重名残留（理论不会）：按名反查
        row = (await session.execute(text(
            "SELECT id FROM users WHERE name = :n"), {"n": name})).scalar()
        return row


async def _seed(styles: list[tuple]):
    """[(名, keywords, desc, owner_uid_or_None, enabled)]；先清空本表。"""
    async with SessionLocal() as session:
        await session.execute(delete(StyleKeyword))
        for name, kw, desc, owner, en in styles:
            session.add(StyleKeyword(style_name=name, keywords=kw,
                                     description=desc, owner_id=owner,
                                     enabled=en))
        await session.commit()


@pytest.mark.asyncio
async def test_list_scopes_mine_plus_public():
    """GET /api/styles：有效用户看 我的+公共；匿名只看公共。"""
    from src.api.styles import list_styles
    u = await _mk_user(f"u1-{_uniq()}")
    other = await _mk_user(f"u2-{_uniq()}")
    await _seed([
        ("公共甲", "x", "d-pub", None, True),
        ("个人乙", "y", "d-mine", u, True),
        ("他人丙", "z", "d-other", other, True),
    ])
    r = await list_styles(actor="")  # 匿名（qvp_test 无会话）
    names = {i["style_name"] for i in r["items"]}
    assert "公共甲" in names and "个人乙" not in names and "他人丙" not in names


@pytest.mark.asyncio
async def test_upsert_scoping_and_permission():
    from src.api.styles import upsert_style, StyleIn
    from fastapi import HTTPException
    user_a, admin = f"ua-{_uniq()}", f"ad-{_uniq()}"
    uid_a = await _mk_user(user_a)
    uid_admin = await _mk_user(admin, role="admin")
    # 普通用户写个人库
    r = await upsert_style(StyleIn(style_name="我的风格", keywords="k", description="d"),
                           actor=user_a)
    assert r["ok"]
    # 同 owner 幂等覆盖，不新增
    await upsert_style(StyleIn(style_name="我的风格", keywords="k2", description="d2"),
                       actor=user_a)
    async with SessionLocal() as session:
        rows = (await session.execute(select(StyleKeyword).where(
            StyleKeyword.owner_id == uid_a))).scalars().all()
    assert len(rows) == 1 and rows[0].keywords == "k2"
    # 普通用户写公共库 → 403
    with pytest.raises(HTTPException) as e:
        await upsert_style(StyleIn(style_name="P", public=True), actor=user_a)
    assert e.value.status_code == 403
    # admin 写公共库 OK；同名与个人库不冲突（两级唯一）
    await upsert_style(StyleIn(style_name="我的风格", description="pub版", public=True),
                       actor=admin)
    async with SessionLocal() as session:
        n = len((await session.execute(select(StyleKeyword).where(
            StyleKeyword.style_name == "我的风格"))).scalars().all())
    assert n == 2  # 个人一份 + 公共一份
    # 未知 actor → 401
    with pytest.raises(HTTPException) as e:
        await upsert_style(StyleIn(style_name="X"), actor="nobody")
    assert e.value.status_code == 401


@pytest.mark.asyncio
async def test_delete_permission_matrix():
    from src.api.styles import delete_style
    from fastapi import HTTPException
    ua, ub, ad = f"da-{_uniq()}", f"db-{_uniq()}", f"dd-{_uniq()}"
    uid_a, uid_b, uid_ad = await _mk_user(ua), await _mk_user(ub), await _mk_user(ad, role="admin")
    async with SessionLocal() as session:
        pub = StyleKeyword(style_name="公共风格", owner_id=None)
        mine = StyleKeyword(style_name="A的风格", owner_id=uid_a)
        session.add_all([pub, mine])
        await session.commit()
        pub_id, mine_id = str(pub.id), str(mine.id)
    # 普通用户删公共 → 403；删他人 → 403；删自己的 → OK；admin 删公共 → OK
    with pytest.raises(HTTPException) as e:
        await delete_style(pub_id, actor=ua)
    assert e.value.status_code == 403
    with pytest.raises(HTTPException) as e:
        await delete_style(mine_id, actor=ub)
    assert e.value.status_code == 403
    assert (await delete_style(mine_id, actor=ua))["ok"]
    assert (await delete_style(pub_id, actor=ad))["ok"]


@pytest.mark.asyncio
async def test_default_style_pins_selection():
    """钉选直通：users.default_style 命中时 select_image_style 不再随机。"""
    from src.services.style_select import select_image_style
    uname = f"pin-{_uniq()}"
    uid = await _mk_user(uname)
    await _seed([("钉选风格", "无关词", "钉选描述", uid, True),
                 ("公共风格", "无关词", "公共描述", None, True)])
    async with SessionLocal() as session:
        await session.execute(text(
            "UPDATE users SET default_style = '钉选风格' WHERE id = :u"),
            {"u": str(uid)})
        await session.commit()
    for _ in range(6):
        n, d = await select_image_style("任意题材", owner_id=uid)
        assert (n, d) == ("钉选风格", "钉选描述")
    # 未钉选用户仍走候选（个人库优先于公共库）
    n, d = await select_image_style("任意题材", owner_id=None)
    assert n == "公共风格"
    # 清钉选后恢复随机候选
    async with SessionLocal() as session:
        await session.execute(text(
            "UPDATE users SET default_style = NULL WHERE id = :u"), {"u": str(uid)})
        await session.commit()
    n, _ = await select_image_style("任意题材", owner_id=uid)
    assert n == "钉选风格"  # 个人库仅一条


@pytest.mark.asyncio
async def test_personal_library_priority_over_public():
    """个人库非空时优先于公共库（且不影响他人走公共库）。"""
    from src.services.style_select import select_image_style
    ua = f"pr-{_uniq()}"
    uid_a = await _mk_user(ua)
    await _seed([("公共甲", "topic", "公共描述", None, True),
                 ("个人乙", "topic", "个人描述", uid_a, True)])
    n, d = await select_image_style("topic", owner_id=uid_a)
    assert (n, d) == ("个人乙", "个人描述")
    n2, d2 = await select_image_style("topic", owner_id=None)
    assert (n2, d2) == ("公共甲", "公共描述")
    # 停用个人条目 → 该用户回退公共库
    async with SessionLocal() as session:
        await session.execute(text(
            "UPDATE style_keywords SET enabled = false WHERE style_name = '个人乙'"))
        await session.commit()
    n3, _ = await select_image_style("topic", owner_id=uid_a)
    assert n3 == "公共甲"


@pytest.mark.asyncio
async def test_stats_endpoint_aggregates():
    from src.api.styles import style_stats
    uname = f"st-{_uniq()}"
    uid = await _mk_user(uname)
    tids = []
    async with SessionLocal() as session:
        for st in ("approved", "approved", "rejected", "review"):
            tid = uuid.uuid4()
            session.add(Task(id=tid, idempotency_key=f"k-{_uniq()}", query="q",
                             content_type="generic", mode="general", status=st,
                             created_by=uid, gen_image_style="统计风格",
                             gen_image_style_desc="d"))
            tids.append(tid)
        await session.flush()   # 先落任务行再加资产（FK 时序）
        # 历史替换版本（重生成计数）+ 在用版本
        session.add(Asset(task_id=tids[0], page_index=1, source_type="ai_generated",
                          copyright_status="clear", image_url="u1", hash="h1",
                          is_history=True))
        session.add(Asset(task_id=tids[0], page_index=1, source_type="ai_generated",
                          copyright_status="clear", image_url="u2", hash="h2",
                          is_history=False))
        await session.commit()
    try:
        r = await style_stats(actor=uname)
        row = next(i for i in r["items"] if i["style_name"] == "统计风格")
        assert row["total"] == 4 and row["approved"] == 2 and row["rejected"] == 1
        assert row["approval_rate"] == round(2 / 3, 4)
        assert row["regen_count"] == 1
        assert r["default_style"] is None
    finally:
        async with SessionLocal() as session:
            await session.execute(delete(Asset).where(Asset.task_id.in_(tids)))
            await session.execute(delete(Task).where(Task.id.in_(tids)))
            await session.commit()


@pytest.mark.asyncio
async def test_set_and_clear_default():
    from src.api.styles import set_default_style, clear_default_style, DefaultStyleIn
    uname = f"df-{_uniq()}"
    await _mk_user(uname)
    r = await set_default_style(DefaultStyleIn(style_name="我的最爱"), actor=uname)
    assert r["default_style"] == "我的最爱"
    async with SessionLocal() as session:
        v = (await session.execute(text(
            "SELECT default_style FROM users WHERE name = :n"), {"n": uname})).scalar()
    assert v == "我的最爱"
    r2 = await clear_default_style(actor=uname)
    assert r2["default_style"] is None


@pytest.mark.asyncio
async def test_extract_page_subjects_and_prompt_injection():
    """page_subject：解析容错 + 注入提示词替换通用锚定句。"""
    from src.services.page_subject import extract_page_subjects
    from src.gateway.prompt_versions import get_image_prompt

    async def fake_llm(prompt):
        return {"text": '["加冰块的奶油风高脚杯", "手冲咖啡特写", "两只碰杯的手特写",'
                        ' "餐桌上的甜品拼盘", "厨房料理过程", "朋友聚会举杯合影"]'}

    bodies = [f"第{i}页文案内容" for i in range(1, 7)]
    subjects = await extract_page_subjects(bodies, llm_call=fake_llm)
    assert subjects and len(subjects) == 6 and subjects[0] == "加冰块的奶油风高脚杯"

    p = get_image_prompt("general", "x", 1, page_subject=subjects[0])
    assert "本页画面主体必须是：加冰块的奶油风高脚杯" in p
    assert "画面主体必须直接描绘本页文案所讲的事物本身" not in p  # 通用句已替换
    # 无主体 → 保留通用锚定句
    p2 = get_image_prompt("general", "x", 1)
    assert "画面主体必须直接描绘本页文案所讲的事物本身" in p2

    # 数量不对/解析失败 → None 不抛异常
    async def bad_llm(prompt):
        return {"text": "不是JSON"}
    assert await extract_page_subjects(bodies, llm_call=bad_llm) is None
    async def short_llm(prompt):
        return {"text": '["a", "b"]'}
    assert await extract_page_subjects(bodies, llm_call=short_llm) is None
