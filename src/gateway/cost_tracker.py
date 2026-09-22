"""动态费率核算（2026-09-09 改造）。

- 费率权威在 DB `model_rates` 表（迁移 020），管理页/改库即生效零发版：
  进程内缓存 60s，异步上下文调用 `refresh_rates()` 刷新；DB 不可用或未迁移时
  降级到 `DEFAULT_RATES` 代码兜底（与种子数据同值）。
- 计费口径（DeepSeek 官方文档 2026-09-09）：
  扣费 = token × 单价；输入按「缓存命中/未命中」分段计价；
  高峰 = 北京时间（UTC+8，与服务器时区无关）周一至周五 9:00-12:00、14:00-18:00，
  其余为空闲时段按 `offpeak_ratio` 折扣（DeepSeek 官方半价 0.5）。
- 兼容旧调用：三参 `estimate_cost(model, prompt, completion)` 不炸，
  未传 cache_hit_tokens 时按全部未命中计（上游 usage 不带缓存拆分时）。
"""
import time
from datetime import datetime, timedelta, timezone

# 代码兜底费率（与 migrations/020_model_rates.sql 种子数据同值）
DEFAULT_RATES: dict[str, dict] = {
    "deepseek-v4-pro": {"label": "DeepSeek V4-Pro（官方价 2026-09-09）",
                        "input_hit_peak": 0.30, "input_miss_peak": 9.0,
                        "output_peak": 27.0, "offpeak_ratio": 0.5, "per_call_cny": 0.0},
    "deepseek-v4-flash": {"label": "DeepSeek V4-Flash（官方价 2026-09-09，现役主模型）",
                          "input_hit_peak": 0.10, "input_miss_peak": 3.0,
                          "output_peak": 9.0, "offpeak_ratio": 0.5, "per_call_cny": 0.0},
    "k3": {"label": "Kimi K3 旗舰（2026-08-14 旧价待校准）",
           "input_hit_peak": 20.0, "input_miss_peak": 20.0,
           "output_peak": 100.0, "offpeak_ratio": 1.0, "per_call_cny": 0.0},
    "kimi-k2.6": {"label": "Kimi K2.6（2026-09-16 参考价 ¥7/29 待账单校准）",
                  "input_hit_peak": 1.0, "input_miss_peak": 7.0,
                  "output_peak": 29.0, "offpeak_ratio": 1.0, "per_call_cny": 0.0},
    "qwen-vl-ocr": {"label": "百炼 qwen-vl-ocr（2026-08-20 官网价）",
                    "input_hit_peak": 0.3, "input_miss_peak": 0.3,
                    "output_peak": 0.5, "offpeak_ratio": 1.0, "per_call_cny": 0.0},
    "gpt-4o": {"label": "GPT-4o（按汇率 7.2 估算）",
               "input_hit_peak": 18.0, "input_miss_peak": 18.0,
               "output_peak": 72.0, "offpeak_ratio": 1.0, "per_call_cny": 0.0},
    "gpt-image-2": {"label": "gpt-image-2 生图（按次计费，通道基准价）",
                    "input_hit_peak": 0.0, "input_miss_peak": 0.0,
                    "output_peak": 0.0, "offpeak_ratio": 1.0, "per_call_cny": 0.2},
    # 分通道按次价（021，2026-09-09）：fusion 为用户账单实证 0.2 元/张，
    # 其余通道参照 fusion 待校准
    "gpt-image-2@fusion": {"label": "gpt-image-2 · FusionAI（账单实证）",
                           "input_hit_peak": 0.0, "input_miss_peak": 0.0,
                           "output_peak": 0.0, "offpeak_ratio": 1.0, "per_call_cny": 0.2},
    "gpt-image-2@moacode": {"label": "gpt-image-2 · Moacode（参照 fusion 待校准）",
                            "input_hit_peak": 0.0, "input_miss_peak": 0.0,
                            "output_peak": 0.0, "offpeak_ratio": 1.0, "per_call_cny": 0.2},
    "gpt-image-2@openox": {"label": "gpt-image-2 · Openox（参照 fusion 待校准）",
                           "input_hit_peak": 0.0, "input_miss_peak": 0.0,
                           "output_peak": 0.0, "offpeak_ratio": 1.0, "per_call_cny": 0.2},
    # gpt-image-2.5 系列（025，2026-09-14）：默认 flare，sunburst 同价待校准
    "gpt-image-2.5-flare": {"label": "gpt-image-2.5-flare 生图（按次计费，通道基准价）",
                            "input_hit_peak": 0.0, "input_miss_peak": 0.0,
                            "output_peak": 0.0, "offpeak_ratio": 1.0, "per_call_cny": 0.2},
    "gpt-image-2.5-flare@fusion": {"label": "gpt-image-2.5-flare · FusionAI（参照 gpt-image-2 待校准）",
                                   "input_hit_peak": 0.0, "input_miss_peak": 0.0,
                                   "output_peak": 0.0, "offpeak_ratio": 1.0, "per_call_cny": 0.2},
    "gpt-image-2.5-flare@moacode": {"label": "gpt-image-2.5-flare · Moacode（参照 fusion 待校准）",
                                    "input_hit_peak": 0.0, "input_miss_peak": 0.0,
                                    "output_peak": 0.0, "offpeak_ratio": 1.0, "per_call_cny": 0.2},
    "gpt-image-2.5-flare@openox": {"label": "gpt-image-2.5-flare · Openox（参照 fusion 待校准）",
                                   "input_hit_peak": 0.0, "input_miss_peak": 0.0,
                                   "output_peak": 0.0, "offpeak_ratio": 1.0, "per_call_cny": 0.2},
    "gpt-image-2.5-sunburst": {"label": "gpt-image-2.5-sunburst 生图（按次计费，通道基准价）",
                               "input_hit_peak": 0.0, "input_miss_peak": 0.0,
                               "output_peak": 0.0, "offpeak_ratio": 1.0, "per_call_cny": 0.2},
    "gpt-image-2.5-sunburst@fusion": {"label": "gpt-image-2.5-sunburst · FusionAI（参照 gpt-image-2 待校准）",
                                      "input_hit_peak": 0.0, "input_miss_peak": 0.0,
                                      "output_peak": 0.0, "offpeak_ratio": 1.0, "per_call_cny": 0.2},
    "gpt-image-2.5-sunburst@moacode": {"label": "gpt-image-2.5-sunburst · Moacode（参照 fusion 待校准）",
                                       "input_hit_peak": 0.0, "input_miss_peak": 0.0,
                                       "output_peak": 0.0, "offpeak_ratio": 1.0, "per_call_cny": 0.2},
    "gpt-image-2.5-sunburst@openox": {"label": "gpt-image-2.5-sunburst · Openox（参照 fusion 待校准）",
                                      "input_hit_peak": 0.0, "input_miss_peak": 0.0,
                                      "output_peak": 0.0, "offpeak_ratio": 1.0, "per_call_cny": 0.2},
    "default": {"label": "兜底费率（未识别模型）",
                "input_hit_peak": 3.0, "input_miss_peak": 3.0,
                "output_peak": 6.0, "offpeak_ratio": 1.0, "per_call_cny": 0.0},
}

_BEIJING = timezone(timedelta(hours=8))
# 高峰窗口（北京时间，[起, 止) 按小时）：工作日 9:00-12:00、14:00-18:00
_PEAK_HOURS = ((9, 12), (14, 18))

# 进程内费率缓存（DB → 内存，TTL 60s）
_rates_cache: dict[str, dict] = {}
_rates_loaded_at: float = 0.0
_RATES_TTL = 60.0


def is_peak_time(at: datetime | None = None) -> bool:
    """是否高峰时段（北京时间工作日 9-12 点、14-18 点；与服务器本地时区无关）。"""
    at = at or datetime.now(timezone.utc)
    bj = at.astimezone(_BEIJING)
    if bj.weekday() >= 5:  # 周六/周日全天空闲
        return False
    return any(start <= bj.hour < end for start, end in _PEAK_HOURS)


def resolve_model_key(model: str) -> str:
    """把各路模型标识（deepseek/deepseek-chat、dsh:k3、dsh:deepseek-v4-flash…）
    归一到 model_rates.model_key。"""
    m = (model or "").lower().split(":")[-1].split("/")[-1]
    if "ocr" in m:
        return "qwen-vl-ocr"
    if "deepseek" in m and "flash" in m:
        return "deepseek-v4-flash"
    if "deepseek" in m:
        return "deepseek-v4-pro"
    if "k2.6" in m or "kimi-k2-6" in m:
        return "kimi-k2.6"
    if "moonshot" in m or "kimi" in m or "k3" in m:
        return "k3"
    if "gpt-image-2.5" in m:
        if "sunburst" in m:
            return "gpt-image-2.5-sunburst"
        return "gpt-image-2.5-flare"
    if "gpt-image" in m:
        return "gpt-image-2"
    if "gpt" in m:
        return "gpt-4o"
    return "default"


def get_rate(model_key: str) -> dict:
    """取当前生效费率（DB 缓存优先，代码兜底）。"""
    return (_rates_cache.get(model_key)
            or DEFAULT_RATES.get(model_key)
            or DEFAULT_RATES["default"])


async def refresh_rates(force: bool = False) -> None:
    """从 model_rates 表刷新进程内缓存（TTL 60s；DB 失败保留旧缓存/兜底，不炸调用方）。"""
    global _rates_cache, _rates_loaded_at
    now = time.monotonic()
    if not force and _rates_cache and (now - _rates_loaded_at) < _RATES_TTL:
        return
    try:
        from sqlalchemy import text
        from src.db.session import SessionLocal
        async with SessionLocal() as session:
            rows = (await session.execute(text(
                "SELECT model_key, label, input_hit_peak, input_miss_peak,"
                " output_peak, offpeak_ratio, per_call_cny FROM model_rates"))).all()
        if rows:
            _rates_cache = {
                r[0]: {"label": r[1], "input_hit_peak": float(r[2]),
                       "input_miss_peak": float(r[3]), "output_peak": float(r[4]),
                       "offpeak_ratio": float(r[5]), "per_call_cny": float(r[6])}
                for r in rows}
            _rates_loaded_at = now
    except Exception:  # noqa: BLE001
        pass  # 表未迁移/DB 不可达：保留旧缓存或代码兜底


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int,
                  cache_hit_tokens: int | None = None,
                  at_time: datetime | None = None) -> float:
    """按当前费率估算一次模型调用的费用（元）。

    - cache_hit_tokens：输入中命中缓存的 token 数；None=上游未拆分，按全部未命中计。
    - at_time：调用时刻（高峰/空闲判定，默认现在）；仅 offpeak_ratio<1 的费率有空闲折扣。
    """
    rate = get_rate(resolve_model_key(model))
    hit = max(0, min(int(cache_hit_tokens or 0), int(prompt_tokens)))
    miss = int(prompt_tokens) - hit
    ratio = 1.0 if is_peak_time(at_time) else float(rate["offpeak_ratio"])
    return ((hit / 1_000_000 * rate["input_hit_peak"])
            + (miss / 1_000_000 * rate["input_miss_peak"])
            + (int(completion_tokens) / 1_000_000 * rate["output_peak"])) * ratio


def per_call_cost(model: str, fallback: float = 0.0) -> float:
    """按次计费通道（如生图）的单价（元/次）。

    精确匹配先行（如 gpt-image-2.5-flare@fusion 通道行）；无该行时回落到基础模型行
    （gpt-image-2.5-flare 基准价）；仍为 0 时返回 fallback（调用方传
    settings.image_cost_per_image_cny 作全局兜底）。
    """
    m = (model or "").lower()
    rate = _rates_cache.get(m) or DEFAULT_RATES.get(m)
    if rate and rate["per_call_cny"]:
        return float(rate["per_call_cny"])
    base = get_rate(resolve_model_key(m))
    if base["per_call_cny"]:
        return float(base["per_call_cny"])
    return float(fallback)


# ── 费用分类（/api/admin/costs 四类拆分 + 余额台账扣减，2026-09-09）──
CATEGORY_LABELS = {"text_llm": "文本模型", "image_gen": "生图",
                   "search": "搜索", "ocr": "OCR"}

# 节点名 → 类别（优先于模型名归类）
_NODE_CATEGORY = {
    "asset_gen": "image_gen", "asset_regen": "image_gen",
    "ocr_read": "ocr",
    "entity_bind": "search", "evidence_build": "search",
    "ref_seed": "search", "ref_collect": "search",
}

# MCP 工具 kind → 类别（tool_ledger 台账口径）
_TOOL_KIND_CATEGORY = {
    "web_search": "search", "image_search": "search",
    "generate_images": "image_gen", "image_gen": "image_gen",
    "ocr": "ocr",
}


def tool_kind_category(kind: str) -> str:
    """tool_ledger 的 kind 归类；LLM 类/校验类能力工具统一 text_llm。"""
    return _TOOL_KIND_CATEGORY.get(kind, "text_llm")


def classify_category(node_name: str, model_version: str | None = None) -> str:
    """node_events 计费行归类：节点名优先，其次模型名，兜底 text_llm。"""
    if node_name in _NODE_CATEGORY:
        return _NODE_CATEGORY[node_name]
    m = (model_version or "").lower()
    if "ocr" in m:
        return "ocr"
    if "gpt-image" in m:
        return "image_gen"
    return "text_llm"
