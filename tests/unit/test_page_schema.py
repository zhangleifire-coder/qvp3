"""page_schema 结构化分页（v0.1.4 P2）：解析/收敛/兼容层/渲染 + 页数可配。"""
import json

from src.services import page_schema as ps


def _spec(title="狗狗能不能洗澡", subtitle="三个月内幼犬不要洗",
          points=None, subject=" wet毛巾擦拭的幼犬特写",
          info_task="能不能洗：给核心结论"):
    return {"title": title, "subtitle": subtitle,
            "points": points if points is not None else ["疫苗没打齐不要出门", "先用湿毛巾擦身"],
            "subject": subject.strip(), "info_task": info_task}


def test_parse_structured_ok():
    raw = "```json\n" + json.dumps([_spec() for _ in range(6)], ensure_ascii=False) + "\n```"
    specs = ps.parse_pages_json(raw, 6)
    assert specs is not None and len(specs) == 6
    assert specs[0]["subject"] == "wet毛巾擦拭的幼犬特写"[:20].strip()


def test_parse_wrong_count_returns_none():
    raw = json.dumps([_spec() for _ in range(5)], ensure_ascii=False)
    assert ps.parse_pages_json(raw, 6) is None


def test_parse_legacy_string_array_returns_none():
    """结构化解析器不收字符串数组——由调用方走兼容层。"""
    raw = json.dumps(["第1页" * 30] * 6, ensure_ascii=False)
    assert ps.parse_pages_json(raw, 6) is None


def test_parse_clamps_lengths():
    spec = _spec(title="超" * 30, subtitle="过短", points=["超" * 30] * 6,
                 subject="超" * 30, info_task="超" * 100)
    out = ps.normalize_spec(spec)
    assert len(out["title"]) == ps.TITLE_MAX
    assert out["subtitle"] == ""            # 过短副题丢弃
    assert len(out["points"]) == ps.POINTS_LIMIT
    assert all(len(p) <= ps.POINT_MAX for p in out["points"])
    assert len(out["subject"]) <= ps.SUBJECT_MAX


def test_normalize_requires_title():
    import pytest
    with pytest.raises(ValueError):
        ps.normalize_spec({"points": ["x"]})
    with pytest.raises(ValueError):
        ps.normalize_spec("不是对象")


def test_spec_from_plain_matches_split_title_points():
    from src.services.poster_compose import split_title_points
    body = "狗狗洗澡全攻略。三个月内幼犬不要洗，疫苗没打齐前不要出门。水温38度，洗完立刻吹干。"
    t, sub, pts = split_title_points(body)
    spec = ps.spec_from_plain(body)
    assert spec["title"] == t and spec["subtitle"] == sub
    assert spec["points"] == pts
    assert spec["subject"] == ""            # 兼容层不伪造主体


def test_render_page_text():
    spec = _spec()
    rendered = ps.render_page_text(spec)
    lines = rendered.split("\n")
    assert lines[0] == spec["title"]
    assert lines[1] == spec["subtitle"]
    assert lines[2:] == spec["points"]


def test_fill_page_template_5_vs_6():
    tpl = "恰好 {page_count} 页。结构：\n{page_plan}"
    out6 = ps.fill_page_template(tpl, 6)
    out5 = ps.fill_page_template(tpl, 5)
    assert "恰好 6 页" in out6 and "第 6 页结尾" in out6
    assert "恰好 5 页" in out5 and "第 2-4 页" in out5 and "第 5 页结尾" in out5


def test_layout_rotation_5_pages_skips_list_role():
    """5 页布局轮换按模板 §3.2 去掉清单页（index 3）：第 4 页=场景页、第 5 页=总结页。"""
    from src.config import settings
    from src.gateway import prompt_versions as pv

    layouts = pv._PAGE_LAYOUTS
    assert len(layouts) == 6
    old = settings.page_count
    try:
        settings.page_count = 6
        assert pv._layout_for_page(4, layouts) is layouts[3]   # 清单页
        assert pv._layout_for_page(6, layouts) is layouts[5]   # 总结页
        settings.page_count = 5
        assert pv._layout_for_page(1, layouts) is layouts[0]   # 封面
        assert pv._layout_for_page(4, layouts) is layouts[4]   # 场景页（跳过清单）
        assert pv._layout_for_page(5, layouts) is layouts[5]   # 总结页
    finally:
        settings.page_count = old


def test_parse_pages_result_legacy_fallback():
    """nodes._parse_pages_result：旧字符串数组原文照用（specs=None，
    compose 阶段走兼容层）；结构化对象正常消费。"""
    from src.pipeline.nodes import _parse_pages_result
    legacy = json.dumps(["狗狗洗澡全攻略，三个月内不要洗。疫苗打齐前不要出门，水温38度左右，洗完立刻吹干。"] * 6,
                        ensure_ascii=False)
    pages, specs = _parse_pages_result(legacy, 6)
    assert pages is not None and specs is None      # 旧格式零变化：原文照用
    assert len(pages) == 6 and "狗狗洗澡全攻略" in pages[0]
    structured = json.dumps([_spec() for _ in range(6)], ensure_ascii=False)
    pages2, specs2 = _parse_pages_result(structured, 6)
    assert specs2[0]["subject"] != ""
    assert pages2[0].startswith(specs2[0]["title"])
    assert _parse_pages_result("完全不是JSON", 6) == (None, None)


def test_validate_pages_structured(monkeypatch):
    """staged 分页校验：结构化对象通过且 specs 透传。"""
    from src.config import settings
    from src.pipeline import agent_stages as st
    monkeypatch.setattr(settings, "page_count", 6)
    out, errors = st._validate_pages(
        {"pages": [_spec() for _ in range(6)], "notes": "ok"})
    assert errors == [] and out is not None
    assert len(out["specs"]) == 6
    assert out["specs"][0]["info_task"] == "能不能洗：给核心结论"
    assert len(out["pages"]) == 6
    assert out["pages"][0].startswith("狗狗能不能洗澡")
