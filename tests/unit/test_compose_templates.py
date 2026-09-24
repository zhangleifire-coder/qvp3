"""页型模板库（2026-09-24）单测：spec 校验 / 种子合法性 / 选择器轮换 /
VL 分析→spec 映射 / 渲染冒烟（全部 12 预置模板出图）。"""
import pytest

from src.services.compose_templates import (
    validate_spec, load_seeds, spec_signature)
from src.services.template_extract import analysis_to_spec


def test_seeds_all_valid():
    seeds = load_seeds()
    assert len(seeds) >= 11
    for t in seeds:
        assert validate_spec(t) == [], t["template_id"]


def test_validate_rejects_open_vocabulary():
    bad = {"template_id": "x", "name": "x", "page_role": "content",
           "photo": {"arrangement": "diagonal_split", "top_fraction": 0.9},
           "text": {"bg": "neon", "elements": [{"type": "hologram"}]},
           "decor": ["sparkles"]}
    errs = validate_spec(bad)
    assert any("arrangement" in e for e in errs)
    assert any("top_fraction" in e for e in errs)
    assert any("bg" in e for e in errs)
    assert any("元素" in e for e in errs)
    assert any("decor" in e for e in errs)


def test_validate_deep_bg_only_ending():
    base = load_seeds()[0]
    deep = {**base, "template_id": "t2",
            "text": {**base["text"], "bg": "theme_deep"}}
    errs = validate_spec(deep)
    assert any("theme_deep" in e for e in errs)


@pytest.mark.asyncio
async def test_template_select_roles_and_fallback(monkeypatch):
    from src.services import compose_templates as ct
    # DB 不可用 → 种子文件回退：cover/ending/content 各能选出合法 spec
    async def fake_list(enabled_only=False, source=None):
        return [dict(t, enabled=True) for t in ct.load_seeds()]
    monkeypatch.setattr(ct, "list_templates", fake_list)
    tpl1 = await ct.template_select("task-a", 1, 5)
    tpl5 = await ct.template_select("task-a", 5, 5)
    tpl3 = await ct.template_select("task-a", 3, 5)
    assert tpl1["page_role"] == "cover"
    # 末页 = content ∪ ending 池（0923 语料：参考案例第 5 页均为内容形态）
    assert tpl5["page_role"] in ("content", "ending")
    assert tpl3["page_role"] == "content"
    # 确定性：同 task+页码两次选择结果一致
    assert await ct.template_select("task-a", 3, 5) == tpl3


def test_analysis_to_spec_closed_mapping():
    a = {"page_role": "content", "photo_count": 4, "arrangement": "grid_2x2",
         "top_fraction": 0.62, "frame": "polaroid",
         "text_bg": "theme_light", "title_style": "accent_bar",
         "section_style": "number_badge", "body": "paragraph",
         "decor": ["sticker", "dashes", "unknown_thing"],
         "title_text": "四宫格示例", "body_gist": "段落正文"}
    spec, errs = analysis_to_spec(a, "ext_test1", "测试提取")
    assert errs == []
    assert spec["photo"]["arrangement"] == "grid_2x2"
    assert spec["photo"]["frame"] == "polaroid"
    # 未知装饰（unknown_thing）被丢弃而非透传——封闭词汇表
    assert "sticker_br" in spec["decor"]
    assert all("unknown" not in d for d in spec["decor"])
    assert spec["source"] == "vl_extracted"

    # 非法值全部收敛到合法档
    a2 = {"arrangement": "fancy", "top_fraction": 2.0, "frame": "neon",
          "title_style": "3d", "body": "voice", "page_role": "hero"}
    spec2, errs2 = analysis_to_spec(a2, "ext_test2", "收敛")
    assert errs2 == []
    assert spec2["photo"]["arrangement"] == "single"
    assert spec2["photo"]["top_fraction"] == 0.75


def test_spec_signature_dedup():
    a = {"page_role": "content", "arrangement": "single",
         "top_fraction": 0.57, "frame": "none",
         "title_style": "accent_bar", "section_style": "none",
         "body": "paragraph", "decor": [], "title_text": "",
         "body_gist": ""}
    s1, _ = analysis_to_spec(a, "t1", "a")
    b = dict(a, top_fraction=0.58)   # 0.05 档内视为同签名
    s2, _ = analysis_to_spec(b, "t2", "b")
    assert spec_signature(s1) == spec_signature(s2)
    c = dict(a, arrangement="side_by_side")
    s3, _ = analysis_to_spec(c, "t3", "c")
    assert spec_signature(s1) != spec_signature(s3)


def _placeholder_ill(path, color=(200, 180, 160)):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (1024, 1024), color)
    ImageDraw.Draw(im).ellipse([200, 200, 800, 800],
                               outline=(120, 100, 90), width=10)
    im.save(path)
    return path


def test_render_all_seeds_smoke(tmp_path):
    """12 预置模板全部渲染出 3:4 PNG（本地字体回退 Windows/Noto 也必须成功）。"""
    from src.services.compose_renderer import render_card
    from src.services.poster_compose import W, H
    ills = [_placeholder_ill(tmp_path / f"ph{i}.png",
                             (210 - i * 15, 190, 170 + i * 10))
            for i in range(4)]
    content = {"title": "冷淡风更百搭，通勤首选小圆环",
               "section_title": "冷感耳环：通勤主力", "section_no": 2,
               "paragraph": ("上班戴得多的是冷感耳环。银色、枪色、哑光钛钢"
                             "用线条和几何撑气质，直径2到3厘米。"),
               "points": ["直径2-3厘米", "单只不超5克"],
               "subtitle": "通勤优先", "sticker_text": "通勤"}
    for t in load_seeds():
        arr = t["photo"]["arrangement"]
        n = {"single": 1, "side_by_side": 2, "grid_2x2": 4,
             "triple_row": 3, "one_big_two_small": 3, "none": 0}[arr]
        out = render_card(t, content, ills[:max(1, n)],
                          style_desc="", out_path=tmp_path / f"{t['template_id']}.png")
        from PIL import Image
        im = Image.open(out)
        assert im.size == (W, H), t["template_id"]
        assert out.stat().st_size > 10000, t["template_id"]


@pytest.mark.asyncio
async def test_template_select_accepts_uuid_task_id(monkeypatch):
    """compose 路径传 UUID 对象（c665acf3 教训：task_id.encode() 崩 AttributeError
    → compose 整链异常回退直出）——str() 后必须正常出模板。"""
    import uuid as _uuid
    from src.services import compose_templates as ct

    async def fake_list(enabled_only=False, source=None):
        return [dict(t, enabled=True) for t in ct.load_seeds()]
    monkeypatch.setattr(ct, "list_templates", fake_list)
    tid = _uuid.uuid4()
    picked = [await ct.template_select(tid, p, 5) for p in range(1, 6)]
    assert all(p is not None for p in picked)
    # 同套中间页走位取模：不同页尽量不同模板
    tids = [p["template_id"] for p in picked]
    assert len(set(tids[1:4])) >= 2, tids
