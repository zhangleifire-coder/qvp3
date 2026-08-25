"""P0-2 图上文字扭曲质检：相似度算法 + 重生循环逻辑 单测。"""
from src.pipeline.agent_production import (_GARBLE_MAX_REGEN, _GARBLE_THRESHOLD,
                                           _text_similarity)


class TestTextSimilarity:
    def test_identical(self):
        assert _text_similarity("净水器滤芯多久换一次", "净水器滤芯多久换一次") == 1.0

    def test_garbled_below_threshold(self):
        # 1/3 字符扭曲 → 低于 0.82 阈值被拦
        assert _text_similarity("净氺器滤蕊多乆换壹次", "净水器滤芯多久换一次") < _GARBLE_THRESHOLD

    def test_extra_ocr_content_not_penalized(self):
        # OCR 多识别出的背景文字不扣分（按文案命中计）
        assert _text_similarity("净水器滤芯多久换一次 PP棉3-6个月", "净水器滤芯多久换一次") == 1.0

    def test_empty_ocr_is_failure(self):
        assert _text_similarity("", "净水器滤芯多久换一次") == 0.0

    def test_empty_copy_passes(self):
        assert _text_similarity("任意", "") == 1.0

    def test_punctuation_ignored(self):
        assert _text_similarity("滤芯更换周期 仅供参考", "滤芯更换周期，仅供参考！") == 1.0

    def test_missing_chars_penalized(self):
        assert _text_similarity("净水器滤芯", "净水器滤芯多久换一次") == 0.5


class TestGarbleConfig:
    def test_threshold_and_regen_limit(self):
        assert 0.7 <= _GARBLE_THRESHOLD <= 0.9   # OCR 噪声容错区间
        assert 1 <= _GARBLE_MAX_REGEN <= 3        # 有限重生成，防无限烧钱
