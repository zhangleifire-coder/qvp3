from src.gateway.prompt_versions import get_draft_prompt, get_image_prompt


def test_draft_prompt_per_mode():
    assert "对比" in get_draft_prompt("compare")
    assert "单品" in get_draft_prompt("single")
    assert "图文内容" in get_draft_prompt("general")


def test_draft_prompt_unknown_mode_falls_back_to_general():
    assert get_draft_prompt("nope") == get_draft_prompt("general")


def test_image_prompt_fills_page_body():
    p = get_image_prompt("general", "本页是测试文案")
    assert "本页是测试文案" in p
    assert "{page_body}" not in p


def test_image_prompt_per_mode():
    assert "两个主体" in get_image_prompt("compare", "")
    assert "参考实景图" in get_image_prompt("single", "")
    assert "无参考图" in get_image_prompt("general", "")
