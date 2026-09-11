"""MCP 工具服务器入口：FastMCP stdio 模式，由 Nanobot 作为子进程拉起。

启动要求：PYTHONPATH 指向 code/ 根目录（复用 src.gateway 与 .env 配置）。
Nanobot 端配置见 nanobot/config.json 的 tools.mcpServers."qvp-tools"。
"""
import asyncio
import hashlib
import io
import uuid
from pathlib import Path

from fastmcp import FastMCP

from src.config import settings
from src.gateway.image_gen import generate_image
from src.gateway.image_search import search_image as _search_image_impl
from src.gateway.ocr import fetch_image_bytes, ocr_image as _ocr_impl
from src.gateway.prompt_versions import get_image_prompt
from src.gateway.web_search import web_search as _web_search_impl

from .cost_report import report_usage
from .quotas import check_and_consume

mcp = FastMCP("qvp-tools")

# 与后端 static/generated 同一目录（同机部署，磁盘共享）
GENERATED_DIR = Path(__file__).resolve().parent.parent / "static" / "generated"

_EXT_BY_CTYPE = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


def _persist_image(task_id, page_index: int, tag: str, data: bytes, ctype: str) -> str:
    """图片字节落盘到 static/generated/，返回 /static/generated/... 本地路径。

    与后端 src/pipeline/nodes.py 的 _persist_image 保持一致：
    上游生图 URL 会过期（隔天 404），必须产出即本地化。
    """
    ext = _EXT_BY_CTYPE.get(ctype, ".png")
    name = f"{task_id}_{tag}{page_index}_{uuid.uuid4().hex[:8]}{ext}"
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    (GENERATED_DIR / name).write_bytes(data)
    return f"/static/generated/{name}"


def _size_ok(data: bytes) -> bool:
    """宽高比 3:4 校验（±5% 容差），失败只标记不重生成（导出时会统一归一）。"""
    try:
        from PIL import Image
        w, h = Image.open(io.BytesIO(data)).size
        return abs(w / h - 0.75) <= 0.05
    except Exception:  # noqa: BLE001
        return True


@mcp.tool
async def web_search(query: str, task_id: str, count: int = 6) -> dict:
    """网页搜索，为内容创作检索事实证据（标题/链接/摘要）。

    每任务配额有限（默认 3 次），请在检索词精准的前提下少用。
    """
    await check_and_consume(task_id, "web_search")
    results = await _web_search_impl(query, count=count)
    cost = settings.doubao_search_cost_per_call
    await report_usage(task_id, "web_search", cost, {"query": query, "count": len(results)})
    return {"results": results, "cost_cny": cost}


@mcp.tool
async def image_search(query: str, task_id: str, count: int = 6) -> dict:
    """搜索网络实景图/实物图（compare/single 模式用作图生图参考图）。"""
    await check_and_consume(task_id, "image_search")
    results = await _search_image_impl(query, count=count)
    cost = settings.openserp_cost_per_call
    await report_usage(task_id, "image_search", cost, {"query": query, "count": len(results)})
    return {"results": results, "cost_cny": cost}


@mcp.tool
async def ocr_image(image_url: str, task_id: str) -> dict:
    """OCR 识别一张图片上的全部文字（用于图上文字与分页文案一致性自检）。

    image_url 传 generate_images 返回的本地路径（/static/generated/...）即可。
    """
    await check_and_consume(task_id, "ocr")
    if settings.mock_image_gen:
        # mock 生图是占位 SVG，视觉模型打不开（实测 400）；直接返回占位文本
        return {"raw_text": "[mock] 开发模式模拟生图，无真实文字",
                "model": "mock", "cost_cny": 0}
    r = await _ocr_impl(image_url)
    await report_usage(task_id, "ocr", r["cost_cny"], {"image_url": image_url[:200]})
    return {"raw_text": r["raw_text"], "model": r["model"], "cost_cny": r["cost_cny"]}


def _batches(items: list, n: int) -> list[list]:
    """把 items 切成每批至多 n 个的批次（并行生图用；n<=1 即单元素批）。"""
    n = max(1, int(n))
    return [items[k:k + n] for k in range(0, len(items), n)]


@mcp.tool
async def generate_images(task_id: str, pages: list[str], mode: str = "general",
                          image_template: str = "", reference_urls: list[str] | None = None) -> dict:
    """按 6 页分页文案批量生成交付配图（内部完成提示词组装/并行生成/去重/本地化/尺寸校验）。

    参数：
    - pages: 6 页图上文案（顺序即页序 1-6）
    - mode: 生产模式 general/single/compare
    - image_template: 后端下发、来自提示词库的生图模板（含 {page_body} 占位）；原样传入
    - reference_urls: compare/single 模式的实景参考图 URL（来自 image_search 结果）

    返回每页 {page_index, prompt, image_url(本地路径), origin_url, hash, size_ok}。
    同任务生图配额有限（默认 8 张，含去重重生），失败页会在 warnings 里说明。
    """
    pages = [str(p or "").strip() for p in (pages or [])]
    if not pages:
        raise ValueError("pages 不能为空：请传入 6 页分页文案")
    await check_and_consume(task_id, "image", n=len(pages))

    reference_urls = [u for u in (reference_urls or []) if u]
    total_pages = len(pages)

    async def _gen_one(i: int, body: str) -> dict:
        prompt = get_image_prompt(mode, body, i, template=image_template or None)
        # 服务器跨国网络抖动（ReadTimeout 等）：单图最多 3 次尝试，避免一张失败炸整批
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                r = await generate_image(prompt, reference_image_urls=reference_urls or None)
                origin_url = r["image_url"]
                data, ctype = await fetch_image_bytes(origin_url)
                await report_usage(task_id, "image_gen_progress", 0,
                                   {"page": i, "total": total_pages})
                return {"page_index": i, "prompt": prompt, "origin_url": origin_url,
                        "data": data, "ctype": ctype,
                        "hash": hashlib.md5(data).hexdigest(),
                        "channel": r.get("channel", "")}
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt < 2:
                    await asyncio.sleep(3 * (attempt + 1))
        raise RuntimeError(f"第{i}页生图 3 次尝试均失败: {last_err}") from last_err

    # 并行分批生成（IMAGE_GEN_PARALLEL 控制批量，批间隔防限流）；
    # 单页失败不炸整批：聚合后明确报出失败页，Agent 无需整批重试（配额有限）
    page_list = list(enumerate(pages, start=1))
    results: dict[int, dict] = {}
    failed_pages: list[str] = []
    batches = _batches(page_list, settings.image_gen_parallel)
    for bi, batch in enumerate(batches):
        outs = await asyncio.gather(*[_gen_one(i, b) for i, b in batch],
                                    return_exceptions=True)
        for (i, _b), r in zip(batch, outs):
            if isinstance(r, BaseException):
                failed_pages.append(f"第{i}页: {r}")
            else:
                results[r["page_index"]] = r
        if bi < len(batches) - 1:
            await asyncio.sleep(settings.image_gen_delay_seconds)
    if failed_pages:
        raise RuntimeError(
            "部分页生图失败（每页已重试 3 次，勿整批重试，请稍后单页补生成或结束本轮）: "
            + "；".join(f[:150] for f in failed_pages))

    # 内容级去重（跨批，按页序）：重复页串行换构图重生一次（配额已含余量）
    seen_hashes: set[str] = set()
    images, warnings = [], []
    extra_gen = 0
    regen_channels: list[str] = []  # 去重重生的实际通道（分通道计费用）
    for i, body in page_list:
        r = results[i]
        if r["hash"] in seen_hashes and not settings.mock_image_gen:
            await check_and_consume(task_id, "image")
            extra_gen += 1
            r2 = await generate_image(
                r["prompt"] + "（请换一种与之前不同的构图和视角）",
                reference_image_urls=reference_urls or None)
            data, ctype = await fetch_image_bytes(r2["image_url"])
            new_hash = hashlib.md5(data).hexdigest()
            if new_hash in seen_hashes:
                warnings.append(f"第{i}页重生后仍重复，请人工复核")
            seen_hashes.add(new_hash)
            regen_channels.append(r2.get("channel", ""))
            await report_usage(task_id, "image_gen_progress", 0,
                               {"page": i, "total": total_pages, "regen": True})
            r = {"page_index": i, "prompt": r["prompt"], "origin_url": r2["image_url"],
                 "data": data, "ctype": ctype, "hash": new_hash,
                 "channel": r2.get("channel", "")}
        seen_hashes.add(r["hash"])
        local_url = _persist_image(task_id, i, "p", r["data"], r["ctype"])
        images.append({
            "page_index": i, "prompt": r["prompt"], "image_url": local_url,
            "origin_url": r["origin_url"] if not r["origin_url"].startswith("data:") else "",
            "hash": r["hash"], "size_ok": _size_ok(r["data"]),
        })

    total = total_pages + extra_gen
    if settings.mock_image_gen:
        cost = 0
    else:
        # 分通道计费（2026-09-09）：成图按各自通道价（gpt-image-2@<channel> 行），
        # 去重重生有实际通道按通道、无行回退基准价 → 全局兜底
        from src.gateway.cost_tracker import per_call_cost
        base_rate = per_call_cost("gpt-image-2",
                                  fallback=settings.image_cost_per_image_cny)
        cost = sum(per_call_cost(f"gpt-image-2@{r.get('channel') or ''}",
                                 fallback=base_rate)
                   for r in results.values())
        cost += sum(per_call_cost(f"gpt-image-2@{c}", fallback=base_rate)
                    for c in regen_channels)
    await report_usage(task_id, "image_gen", cost, {
        "pages": total_pages, "extra_regens": extra_gen, "mode": mode,
        "parallel": settings.image_gen_parallel})
    return {"images": images, "warnings": warnings, "total_generated": total,
            "cost_cny": cost, "mock": settings.mock_image_gen}


def main() -> None:
    mcp.run()  # stdio 传输（Nanobot 以子进程方式拉起）


# qvp_mcp v2（2026-09-03 功能项独立化）：注册 11 个能力工具到上面的 mcp 实例
#（创作类 7 + 校验类 4；导入即注册，实现见 tools_capabilities.py）
from . import tools_capabilities  # noqa: F401,E402

if __name__ == "__main__":
    main()
