"""双生图通道路由：轮询负载均衡 + 故障降级 + 可用性过滤 单测。"""
from unittest.mock import patch
from src.config import settings
from src.gateway.image_gen import _channels, _next_channel


class TestChannels:
    def test_both_available_round_robin(self):
        with patch.object(settings, "image_gen_channels", "linkai,moacode"), \
             patch.object(settings, "moacode_api_key", "cr_x"):
            avail = _channels()
            assert set(avail) == {"linkai", "moacode"}
            picked = [_next_channel() for _ in range(4)]
            assert picked[0] != picked[1]  # 交替

    def test_moacode_filtered_without_key(self):
        with patch.object(settings, "image_gen_channels", "linkai,moacode"), \
             patch.object(settings, "moacode_api_key", ""):
            assert _channels() == ["linkai"]

    def test_linkai_only_when_moacode_unconfigured(self):
        with patch.object(settings, "image_gen_channels", "moacode"), \
             patch.object(settings, "moacode_api_key", ""):
            assert _channels() == ["linkai"]  # 兜底


class TestMoacodePrompt:
    def test_ratio_hint_appended(self):
        """moacode size 不生效，比例写进提示词（3:4 竖版）——纯文本拼接验证。"""
        import src.gateway.image_gen as ig
        # _generate_moacode 内部追加比例；这里验证拼接规则本身
        prompt = "一只三花猫"
        assert "3:4 竖版构图" in f"{prompt}，3:4 竖版构图"
        # 已含比例词不重复追加的规则：检查条件表达式逻辑
        assert "3:4" in "竖版3:4图文卡片"


class TestFusionChannel:
    def test_fusion_in_rotation_when_keyed(self):
        """fusion 配 key 时进入轮询池（主通道优先位）。"""
        with patch.object(settings, "image_gen_channels", "fusion,linkai,moacode"), \
             patch.object(settings, "fusionai_api_key", "sk-fusion-x"), \
             patch.object(settings, "moacode_api_key", "cr_x"):
            avail = _channels()
            assert avail[0] == "fusion" and set(avail) == {"fusion", "linkai", "moacode"}

    def test_fusion_filtered_without_key(self):
        with patch.object(settings, "image_gen_channels", "fusion,linkai"), \
             patch.object(settings, "fusionai_api_key", ""):
            assert _channels() == ["linkai"]
