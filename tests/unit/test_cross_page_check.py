"""跨页批量质检 + 标题-Query 相关性预检（v0.1.4 P3）。"""
import json

from src.services import cross_page_check as cpc


def test_parse_cross_json_ok():
    raw = '```json\n{"info_repeat": true, "visual_repeat": false, "style_consistent": true, "dark_pages": 0, "suspect_pages": [2, 3], "issues": ["第2/3页知识点重复"]}\n```'
    r = cpc._parse_cross_json(raw)
    assert r is not None
    assert r["info_repeat"] is True and r["visual_repeat"] is False
    assert r["suspect_pages"] == [2, 3]
    assert r["dark_pages"] == 0


def test_parse_cross_json_bad():
    assert cpc._parse_cross_json("不是JSON") is None
    assert cpc._parse_cross_json('{"info_repeat": true}') is None  # 缺布尔维度
    # dark_pages 容错为 0，suspects 非数字被剔
    r = cpc._parse_cross_json(
        '{"info_repeat": false, "visual_repeat": false, '
        '"style_consistent": true, "dark_pages": "x", "suspect_pages": ["a", 4]}')
    assert r["dark_pages"] == 0 and r["suspect_pages"] == [4]


def test_cross_flags_marks_suspects_and_globals():
    result = {"ok": False,
              "issues": ["第2/3页知识点重复"],
              "detail": {"suspect_pages": [2, 3], "dark_pages": 2,
                         "info_repeat": True, "visual_repeat": False,
                         "style_consistent": False}}
    flags = cpc.cross_flags(result, 6)
    assert "[跨页]" in flags[2][0] and "[跨页]" in flags[3][0]
    assert any("深色底" in m for m in flags[1])
    assert any("风格不一致" in m for m in flags[1])


def test_cross_flags_ok_empty():
    assert cpc.cross_flags({"ok": True, "issues": [], "detail": {}}, 6) == {}
    assert cpc.cross_flags(None, 6) == {}
    # 越界页码忽略
    r = {"ok": False, "issues": ["x"],
         "detail": {"suspect_pages": [0, 9], "dark_pages": 0,
                    "info_repeat": True, "visual_repeat": False,
                    "style_consistent": True}}
    assert cpc.cross_flags(r, 6) == {}


async def test_build_contact_sheet_persists(monkeypatch, tmp_path):
    """拼版：全部取图成功 → 落盘并返回 URL；任一失败 → None。"""
    import io
    from PIL import Image

    def _png(color):
        buf = io.BytesIO()
        Image.new("RGB", (120, 160), color).save(buf, "PNG")
        return buf.getvalue()

    calls = {"persist": []}

    async def fake_fetch(url):
        return (_png((200, 180, 160)), "image/png")

    def fake_persist(task_id, page_index, prefix, data, mime):
        calls["persist"].append((page_index, prefix, len(data)))
        return "/static/generated/cs_test.png"

    import src.pipeline.nodes as nodes
    import src.gateway.ocr as ocr
    monkeypatch.setattr(ocr, "fetch_image_bytes", fake_fetch)
    monkeypatch.setattr(nodes, "_persist_image", fake_persist)

    url = await cpc.build_contact_sheet("t1", [f"/static/generated/p{i}.png"
                                               for i in range(6)])
    assert url == "/static/generated/cs_test.png"
    assert calls["persist"] == [(0, "cs", calls["persist"][0][2])]  # page 0 + cs 前缀

    async def fail_fetch(url):
        raise RuntimeError("down")

    monkeypatch.setattr(ocr, "fetch_image_bytes", fail_fetch)
    assert await cpc.build_contact_sheet("t1", ["/x.png", "/y.png"]) is None


async def test_query_relevance_check(monkeypatch):
    from src.pipeline import text_check as tc

    async def fake_call(prompt, on_delta=None):
        return {"text": json.dumps(
            {"level": "warn", "reason": "只覆盖了挑选，未覆盖保存方法"},
                ensure_ascii=False),
            "model_version": "kimi-test"}

    monkeypatch.setattr(tc, "call_with_failover", fake_call)
    qr = await tc._query_relevance_check("芒果怎么挑和保存", "标题\n正文" * 100)
    assert qr["level"] == "warn" and "挑选" in qr["reason"]
    assert qr["model"] == "kimi-test"

    async def bad_call(prompt, on_delta=None):
        return {"text": "不是JSON", "model_version": "m"}

    monkeypatch.setattr(tc, "call_with_failover", bad_call)
    assert await tc._query_relevance_check("q", "b" * 100) is None
    assert await tc._query_relevance_check("", "b") is None       # 空 query 跳过
    assert await tc._query_relevance_check("q", "") is None       # 空正文跳过
