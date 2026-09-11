"""ref_collect._collect_candidates 并发化（性能优化 P0-4）单测：
gather + Semaphore(4) 改造后结果集与串行语义逐条等价——
- 下载失败（403 等）剔除、hash 去重、page_index 连续编号、输入序保持；
- OCR 命中词提取与命中计数不变；
- 并发确实生效（最大在途 >1）且不超信号量上限 4；
- 12 张各 50ms 的总耗时显著低于串行（~600ms），有上限断言防回归。

mock：search/fetch/ocr/persist 全 mock；不触 DB（asset_library_reuse 关）。
"""
import asyncio
import time
from unittest.mock import patch

from src.pipeline import ref_collect
from src.pipeline.ref_collect import _collect_candidates

_N = 12
_DELAY = 0.05


def _urls(n=_N):
    return [f"https://img.example.com/c{i}.png" for i in range(n)]


def _fake_search(urls):
    async def search(q, count=6):
        return [{"image_url": u, "title": f"t-{u}", "engine": "bing"}
                for u in urls[:count]]
    return search


class _Inflight:
    """统计 fake 下载/OCR 的最大在途并发数。"""

    def __init__(self):
        self.cur = 0
        self.peak = 0

    async def __call__(self, url, *a, **kw):
        self.cur += 1
        self.peak = max(self.peak, self.cur)
        try:
            await asyncio.sleep(_DELAY)
            return self._result(url)
        finally:
            self.cur -= 1


class _Fetch(_Inflight):
    def _result(self, url):
        if "bad" in url:
            raise RuntimeError("403 forbidden")   # 下载失败剔除
        return url.encode(), "image/png"          # 内容=URL，hash 天然唯一


class _Ocr(_Inflight):
    def _result(self, url):
        # local_url 为 ut_{page_index}.png：奇数页（= 偶数序候选）命中主体词
        import re
        n = int(re.search(r"(\d+)", url.rsplit("/", 1)[1]).group(1))
        hit = "入门咖啡机 简介" if n % 2 == 1 else "无关文字"
        return {"raw_text": hit, "cost_cny": 0, "model": "qwen-vl-ocr"}


def _fake_persist(task_id, page_index, tag, data, ctype):
    return f"/static/generated/ut_{page_index}.png"


async def _run(urls, fetch, ocr):
    with patch("src.gateway.image_search.search_image", new=_fake_search(urls)), \
         patch("src.gateway.ocr.fetch_image_bytes", new=fetch), \
         patch("src.gateway.ocr.ocr_image", new=ocr), \
         patch("src.pipeline.nodes._persist_image", new=_fake_persist), \
         patch("src.config.settings.asset_library_reuse", False):
        return await _collect_candidates("tid-unit", "入门咖啡机怎么选", "single",
                                         "ref_collect")


async def test_concurrent_result_equals_serial_semantics():
    """结果集等价：全部下载成功时 12 张按输入序连续编号，OCR 命中词不变。"""
    fetch, ocr = _Fetch(), _Ocr()
    t0 = time.monotonic()
    cands, hits = await _run(_urls(), fetch, ocr)
    elapsed = time.monotonic() - t0

    assert [c["page_index"] for c in cands] == list(range(1, _N + 1))
    assert [c["origin"] for c in cands] == _urls()          # 输入序保持
    assert [c["local_url"] for c in cands] == [
        f"/static/generated/ut_{i}.png" for i in range(1, _N + 1)]
    assert all(c["title"] and c["engine"] == "bing" and c["hash"] for c in cands)
    assert [c["ocr_hit"] for c in cands] == [
        "入门咖啡机" if i % 2 == 0 else "" for i in range(_N)]
    assert hits == _N // 2
    # 并发生效：峰值在途 >1 且不超信号量 4；总耗时远小于串行 12×(50+50)ms
    assert 1 < fetch.peak <= 4 and 1 < ocr.peak <= 4
    assert elapsed < _N * _DELAY                            # 串行下限的一半


async def test_failure_dropped():
    """下载失败（403）剔除、编号连续，与串行语义一致。"""
    urls = _urls()
    urls[3] = "https://img.example.com/bad.png"      # 下载 403
    fetch, ocr = _Fetch(), _Ocr()

    with patch("src.gateway.image_search.search_image", new=_fake_search(urls)), \
         patch("src.gateway.ocr.fetch_image_bytes", new=fetch), \
         patch("src.gateway.ocr.ocr_image", new=ocr), \
         patch("src.pipeline.nodes._persist_image", new=_fake_persist), \
         patch("src.config.settings.asset_library_reuse", False):
        cands, hits = await _collect_candidates("tid-unit", "入门咖啡机怎么选",
                                                "single", "ref_collect")
    origins = [c["origin"] for c in cands]
    assert "https://img.example.com/bad.png" not in origins   # 403 剔除
    assert len(cands) == _N - 1
    assert [c["page_index"] for c in cands] == list(range(1, _N))  # 连续编号


async def test_exclude_hashes_skip():
    """exclude_hashes（搜图①已有池）命中的候选跳过，与串行一致。"""
    fetch, ocr = _Fetch(), _Ocr()
    import hashlib
    excluded = hashlib.md5(_urls()[0].encode()).hexdigest()
    with patch("src.gateway.image_search.search_image", new=_fake_search(_urls())), \
         patch("src.gateway.ocr.fetch_image_bytes", new=fetch), \
         patch("src.gateway.ocr.ocr_image", new=ocr), \
         patch("src.pipeline.nodes._persist_image", new=_fake_persist), \
         patch("src.config.settings.asset_library_reuse", False):
        cands, hits = await _collect_candidates(
            "tid-unit", "入门咖啡机怎么选", "single", "ref_collect",
            exclude_hashes={excluded})
    assert len(cands) == _N - 1
    assert _urls()[0] not in [c["origin"] for c in cands]
    assert [c["page_index"] for c in cands] == list(range(1, _N))


async def test_mock_mode_skips_ocr_but_keeps_candidates():
    """mock_image_gen=True 时跳过 OCR（不发起调用），候选照常落池。"""
    fetch = _Fetch()
    ocr_called = {"n": 0}

    async def ocr(url):
        ocr_called["n"] += 1
        return {"raw_text": "咖啡机", "cost_cny": 0, "model": "m"}
    with patch("src.gateway.image_search.search_image", new=_fake_search(_urls())), \
         patch("src.gateway.ocr.fetch_image_bytes", new=fetch), \
         patch("src.gateway.ocr.ocr_image", new=ocr), \
         patch("src.pipeline.nodes._persist_image", new=_fake_persist), \
         patch("src.config.settings.asset_library_reuse", False), \
         patch("src.config.settings.mock_image_gen", True):
        cands, hits = await _collect_candidates("tid-unit", "入门咖啡机怎么选",
                                                "single", "ref_collect")
    assert len(cands) == _N and hits == 0 and ocr_called["n"] == 0
    assert all(c["ocr_hit"] == "" for c in cands)


def test_concurrency_constant():
    assert ref_collect._COLLECT_CONCURRENCY == 4
