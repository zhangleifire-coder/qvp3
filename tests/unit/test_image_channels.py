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
