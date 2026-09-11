"""图上文字扭曲质检：100% 标准（归一化后逐字全等）+ 重生循环逻辑 单测。

2026-09-11 用户硬要求：判定口径由「字符相似度 ≥0.82 即过」提升为
「归一化（繁简/异体字 + 全半角 + 去空白）后逐字 100% 相等」。
本文件同时记录 0.82 时代边界用例在新标准下的行为。
"""
from src.pipeline.agent_production import (_GARBLE_MAX_REGEN, _GARBLE_THRESHOLD,
                                           _text_similarity)


class TestTextSimilarity:
    def test_identical(self):
        assert _text_similarity("净水器滤芯多久换一次", "净水器滤芯多久换一次") == 1.0

    def test_garbled_blocked(self):
        # 错字（净氺/滤蕊/多乆/壹）归一化后仍不等 → <1.0 触发重生成
        assert _text_similarity("净氺器滤蕊多乆换壹次", "净水器滤芯多久换一次") < 1.0

    def test_single_wrong_char_blocked(self):
        # 0.82 时代：1 个错字在 30 字文案里相似度 ~0.97 会放行；100% 标准必拦
        copy = "每天早上起床后先喝一杯温水，再开窗通风十分钟"
        ocr = copy.replace("温水", "湿水")
        assert _text_similarity(ocr, copy) < 1.0

    def test_variant_forms_pass(self):
        # 繁简/异体字、全半角、空白差异归一化后视为同一文本 → 放行
        assert _text_similarity("淨水器濾芯多久換一次", "净水器滤芯多久换一次") == 1.0
        assert _text_similarity("滤芯 更换周期", "滤芯更换周期") == 1.0
        assert _text_similarity("占比５０％", "占比50%") == 1.0

    def test_extra_ocr_content_now_blocked(self):
        # 0.82 时代边界用例的新行为：OCR 多识别出的内容（PP棉3-6个月）此前不扣分，
        # 100% 标准要求逐字全等 → 不等触发重生成
        assert _text_similarity("净水器滤芯多久换一次 PP棉3-6个月",
                                "净水器滤芯多久换一次") < 1.0

    def test_punctuation_difference_now_blocked(self):
        # 0.82 时代边界用例的新行为：标点差异此前被忽略，
        # 新标准只做繁简/全半角/去空白归一化 → 标点不一致即拦
        assert _text_similarity("滤芯更换周期 仅供参考", "滤芯更换周期，仅供参考！") < 1.0
        # 全半角标点差异仍放行（全半角统一在归一化内）
        assert _text_similarity("滤芯更换周期,仅供参考!", "滤芯更换周期，仅供参考！") == 1.0

    def test_empty_ocr_is_failure(self):
        assert _text_similarity("", "净水器滤芯多久换一次") == 0.0

    def test_empty_copy_passes(self):
        assert _text_similarity("任意", "") == 1.0

    def test_missing_chars_blocked(self):
        assert _text_similarity("净水器滤芯", "净水器滤芯多久换一次") < 1.0


class TestGarbleConfig:
    def test_threshold_is_exact_match(self):
        assert _GARBLE_THRESHOLD == 1.0   # 100% 标准：归一化后逐字全等才放行
        assert 1 <= _GARBLE_MAX_REGEN <= 3        # 有限重生成，防无限烧钱
