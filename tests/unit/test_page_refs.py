# 参考图按页轮播分配（2026-09-02）：6 张配图实景用法必须错开——
# 不能 6 页全用同一张，也不能每页怼全部参考图。helper 分配逻辑 +
# 双模式提示词硬约束断言。
import pytest

from src.services.style_select import page_refs
from src.gateway.prompt_versions import get_image_prompt


def test_page_refs_carousel():
    refs = ["r1", "r2", "r3", "r4", "r5"]
    subsets = [page_refs(refs, i) for i in range(1, 7)]
    # 每页恰好 2 张
    assert all(len(s) == 2 for s in subsets)
    # 相邻页参考图不同（错开）
    for a, b in zip(subsets, subsets[1:]):
        assert a != b
    # 轮转起点：P1=r1,r2；P2=r2,r3；… P5=r5,r1；P6 回到 P1
    assert subsets[0] == ["r1", "r2"] and subsets[4] == ["r5", "r1"]
    assert subsets[5] == subsets[0]    # 第6页轮转回第1页子集（页数>图数时合理复用）


def test_page_refs_small_pool_passthrough():
    assert page_refs(["r1"], 1) == ["r1"]           # 单张：原样给（允许重复）
    assert page_refs(["r1", "r2"], 4) == ["r1", "r2"]  # 两张：全给
    assert page_refs([], 1) == []
    assert page_refs(None, 1) == []


def test_prefixes_enforce_per_page_rotation():
    # 中文骨架：轮播分配 + 页间各异
    cn = get_image_prompt("single", "文案", 1)
    assert "轮播分配" in cn and "各不相同" in cn
    cn2 = get_image_prompt("compare", "文案", 1)
    assert "本页分配到的参考图" in cn2 and "各不相同" in cn2
    # 英文骨架（visual 模式）同样携带
    en = get_image_prompt("single", "文案", 1, visual="v", style_en="s")
    assert "rotated per page" in en and "differ from those of the other pages" in en
