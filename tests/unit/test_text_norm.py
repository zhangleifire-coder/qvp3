"""错别字/异体字 100% 审核标准：归一化（src/quality/text_norm）单测。

归一化口径 = 繁简/异体字映射 + 全半角统一 + 去空白；
texts_equal_exact：双方归一化后逐字 100% 相等才放行。
"""
from src.quality.text_norm import TRAD2SIMP, normalize_text, texts_equal_exact


class TestMappingTable:
    """归一化映射正确性（常见繁简/异体字对）。"""

    def test_common_pairs(self):
        assert TRAD2SIMP["體"] == "体"
        assert TRAD2SIMP["發"] == "发"
        assert TRAD2SIMP["裡"] == "里"
        assert TRAD2SIMP["臺"] == "台"
        assert TRAD2SIMP["著"] == "着"
        # 映射表完整性：值必须是单字
        assert all(len(k) == len(v) == 1 for k, v in TRAD2SIMP.items())

    def test_traditional_normalized_to_simplified(self):
        assert normalize_text("淨水器濾芯多久換一次") == "净水器滤芯多久换一次"
        assert normalize_text("歷史文物與傳統文化") == "历史文物与传统文化"


class TestNormalize:
    def test_full_width_to_half(self):
        assert normalize_text("２０２６年") == "2026年"
        assert normalize_text("占比５０％") == "占比50%"
        assert normalize_text("ＰＰ棉") == "PP棉"

    def test_whitespace_removed(self):
        assert normalize_text("滤芯 更换\t周期\n仅供参考") == "滤芯更换周期仅供参考"
        assert normalize_text("　全角空格　") == "全角空格"

    def test_full_width_punctuation_unified(self):
        assert normalize_text("周期，仅供参考！") == "周期,仅供参考!"


class TestTextsEqualExact:
    """100% 标准语义：归一化后逐字全等才放行。"""

    def test_exact_passes(self):
        assert texts_equal_exact("净水器滤芯多久换一次", "净水器滤芯多久换一次")

    def test_variant_forms_pass(self):
        # 异体/繁简/全半角差异视为同一文本（双侧同向归一）
        assert texts_equal_exact("淨水器濾芯", "净水器滤芯")
        assert texts_equal_exact("３分钟", "3分钟")

    def test_any_difference_fails(self):
        # 一个错字即不等 → 触发重生成
        assert not texts_equal_exact("净氺器滤蕊多久换一次", "净水器滤芯多久换一次")

    def test_extra_or_missing_content_fails(self):
        assert not texts_equal_exact("净水器滤芯多久换一次 PP棉", "净水器滤芯多久换一次")
        assert not texts_equal_exact("净水器滤芯", "净水器滤芯多久换一次")

    def test_empty_copy_passes_empty_ocr_fails(self):
        assert texts_equal_exact("任意", "")
        assert not texts_equal_exact("", "净水器滤芯多久换一次")
