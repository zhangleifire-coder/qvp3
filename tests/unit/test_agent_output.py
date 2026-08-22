"""Agent 输出契约单测：JSON 防御解析 + 归一化校验。"""
from src.pipeline.agent_production import _parse_agent_json, _validate_output


def _valid_output():
    return {
        "evidence": [{"title": "t", "url": "https://a", "summary": "s"}],
        "draft": "正文" * 100,
        "pages": [f"第{i}页文案内容" for i in range(1, 7)],
        "references": [{"image_url": "https://img/1.png", "title": "r", "engine": "bing"}],
        "images": [{"page_index": i, "image_url": f"/static/generated/p{i}.png"}
                   for i in range(1, 7)],
        "ocr_texts": [{"page_index": 1, "text": "第1页文案内容"}],
        "notes": "ok",
    }


def test_parse_plain_json():
    assert _parse_agent_json('{"a": 1}') == {"a": 1}


def test_parse_fenced_json():
    text = "说明文字\n```json\n{\"a\": [1,2]}\n```\n结尾"
    assert _parse_agent_json(text) == {"a": [1, 2]}


def test_parse_garbage_returns_none():
    assert _parse_agent_json("这不是 JSON") is None
    assert _parse_agent_json("") is None


def test_validate_ok_normalizes():
    out, errors = _validate_output(_valid_output())
    assert errors == []
    assert out["pages"] == [f"第{i}页文案内容" for i in range(1, 7)]
    assert out["images"][0]["page_index"] == 1
    assert out["ocr_map"] == {1: "第1页文案内容"}


def test_validate_pages_wrong_count():
    data = _valid_output()
    data["pages"] = data["pages"][:5]
    out, errors = _validate_output(data)
    assert out is None
    assert any("pages" in e for e in errors)


def test_validate_images_missing_url():
    data = _valid_output()
    data["images"][2]["image_url"] = ""
    out, errors = _validate_output(data)
    assert out is None
    assert any("第 3 项" in e for e in errors)


def test_validate_short_draft():
    data = _valid_output()
    data["draft"] = "太短"
    out, errors = _validate_output(data)
    assert out is None
    assert any("draft" in e for e in errors)


def test_validate_optional_fields_default_empty():
    data = _valid_output()
    data.pop("evidence"); data.pop("references"); data.pop("ocr_texts")
    out, errors = _validate_output(data)
    assert errors == []
    assert out["evidence"] == [] and out["references"] == []
    assert out["ocr_map"] == {}


def test_validate_images_accept_bare_urls():
    data = _valid_output()
    data["images"] = [f"/static/generated/p{i}.png" for i in range(1, 7)]
    out, errors = _validate_output(data)
    assert errors == []
    assert out["images"][5] == {"page_index": 6,
                                "image_url": "/static/generated/p6.png",
                                "origin_url": "", "prompt_used": ""}
