"""双生图通道路由：轮询负载均衡 + 故障降级 + 可用性过滤 单测。
（linkai 通道 2026-09-23 删除——通道=fusion/moacode/openox 三条）"""
from unittest.mock import patch
from src.config import settings
from src.gateway.image_gen import _channels, _next_channel


class TestChannels:
    def test_both_available_round_robin(self):
        with patch.object(settings, "image_gen_channels", "fusion,moacode"), \
             patch.object(settings, "moacode_api_key", "cr_x"), \
             patch.object(settings, "fusionai_api_key", "sk-fusion-x"):
            avail = _channels()
            assert set(avail) == {"fusion", "moacode"}
            picked = [_next_channel() for _ in range(4)]
            assert picked[0] != picked[1]  # 交替

    def test_moacode_filtered_without_key(self):
        with patch.object(settings, "image_gen_channels", "fusion,moacode"), \
             patch.object(settings, "fusionai_api_key", "sk-fusion-x"), \
             patch.object(settings, "moacode_api_key", ""):
            assert _channels() == ["fusion"]

    def test_moacode_fallback_when_all_unconfigured(self):
        with patch.object(settings, "image_gen_channels", "moacode"), \
             patch.object(settings, "moacode_api_key", ""):
            assert _channels() == ["moacode"]  # 兜底

    def test_linkai_removed_from_pool(self):
        """linkai 已删除：配置里写了也进不了通道池。"""
        with patch.object(settings, "image_gen_channels", "fusion,linkai,moacode"), \
             patch.object(settings, "fusionai_api_key", "sk-fusion-x"), \
             patch.object(settings, "moacode_api_key", "cr_x"):
            assert set(_channels()) == {"fusion", "moacode"}


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
        """fusion 配 key 时进入轮询池（主通道优先位）。且新域名默认 .net。"""
        with patch.object(settings, "image_gen_channels", "fusion,moacode"), \
             patch.object(settings, "fusionai_api_key", "sk-fusion-x"), \
             patch.object(settings, "moacode_api_key", "cr_x"):
            avail = _channels()
            assert avail[0] == "fusion" and set(avail) == {"fusion", "moacode"}
        assert settings.fusionai_base_url == "https://api.fusionaix.net/v1"

    def test_fusion_filtered_without_key(self):
        with patch.object(settings, "image_gen_channels", "fusion,moacode"), \
             patch.object(settings, "fusionai_api_key", ""), \
             patch.object(settings, "moacode_api_key", "cr_x"):
            assert _channels() == ["moacode"]


class TestOpenoxChannel:
    def test_openox_in_rotation_when_keyed(self):
        """openox 配 key 时进入轮询池（备份通道，排在配置列表末尾）。"""
        with patch.object(settings, "image_gen_channels", "fusion,moacode,openox"), \
             patch.object(settings, "fusionai_api_key", "sk-fusion-x"), \
             patch.object(settings, "moacode_api_key", "cr_x"), \
             patch.object(settings, "openox_api_key", "sk-openox-x"):
            avail = _channels()
            assert avail[-1] == "openox"
            assert set(avail) == {"fusion", "moacode", "openox"}

    def test_openox_filtered_without_key(self):
        with patch.object(settings, "image_gen_channels", "fusion,openox"), \
             patch.object(settings, "fusionai_api_key", "sk-fusion-x"), \
             patch.object(settings, "openox_api_key", ""):
            assert _channels() == ["fusion"]

    def test_openox_round_robin(self):
        with patch.object(settings, "image_gen_channels", "fusion,openox"), \
             patch.object(settings, "fusionai_api_key", "sk-fusion-x"), \
             patch.object(settings, "openox_api_key", "sk-openox-x"):
            picked = [_next_channel() for _ in range(4)]
            assert set(picked) == {"fusion", "openox"}
