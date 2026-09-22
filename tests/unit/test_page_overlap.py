"""页间相似度机检（v0.1.4 P1.5）：check_page_overlap 中文二元字组 Jaccard。"""
from src.quality.rules import PAGE_OVERLAP_THRESHOLD, check_page_overlap


def test_less_than_two_pages_returns_none():
    assert check_page_overlap([]) is None
    assert check_page_overlap(["只有一页"]) is None
    assert check_page_overlap(["", "  "]) is None


def test_distinct_pages_pass():
    pages = [
        "狗狗能不能洗澡：三个月内幼犬不要洗，疫苗没打齐前出门都危险，先用湿毛巾擦。",
        "洗澡前准备：梳开打结毛发、塞好耳棉、水温38度左右、防滑垫铺在浴缸底。",
        "洗澡手法：顺着毛流揉搓、避开眼周耳道、重点洗腹部脚底、泡泡冲干净不留残留。",
        "洗后吹干：毛巾吸水后低温吹干到摸不到潮气，湿毛闷着容易得皮肤病。",
        "频率建议：短毛犬一个月一次足够，长毛犬两周一次，频繁洗会破坏皮肤油脂层。",
        "总结：洗对澡狗子干净又健康，洗错澡反而生病，收藏这份攻略按步骤来。",
    ]
    r = check_page_overlap(pages)
    assert r is not None and r["passed"] is True
    assert r["details"]["max_jaccard"] < PAGE_OVERLAP_THRESHOLD


def test_duplicated_page_flags():
    base = "狗狗洗澡全攻略：三个月内幼犬不要洗，疫苗打齐前不要出门，水温38度，洗完立刻吹干。"
    pages = [base, base, "完全无关的另一页内容：猫咪铲屎官须知，猫砂盆每天清理一次。"]
    r = check_page_overlap(pages)
    assert r is not None and r["passed"] is False
    assert r["rule_name"] == "page_overlap"
    assert r["details"]["pages"] == [1, 2]
    assert r["details"]["max_jaccard"] > 0.9


def test_threshold_boundary_tunable():
    pages = ["狗狗洗澡注意事项第一页内容", "狗狗洗澡注意事项第二页内容"]
    r_default = check_page_overlap(pages)
    r_loose = check_page_overlap(pages, threshold=0.99)
    assert r_default["details"]["threshold"] == PAGE_OVERLAP_THRESHOLD
    assert r_loose["passed"] is True
