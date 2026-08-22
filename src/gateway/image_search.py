import httpx
from src.config import settings


async def search_image(query: str, count: int = 6) -> list:
    """搜实景图/实物图，按 settings.image_search_provider 路由。

    返回 list[dict]，每个含 title / image_url / source / engine。
    预留 provider 切换：doubao_ark（豆包方舟）、bing_api（必应官方）。
    """
    provider = settings.image_search_provider
    if provider == "openserp":
        return await _search_openserp(query, count)
    if provider == "doubao_ark":
        return await _search_doubao_ark(query, count)
    if provider == "bing_api":
        return await _search_bing_api(query, count)
    raise ValueError(f"unknown image search provider: {provider}")


async def _search_openserp(query: str, count: int) -> list:
    url = f"{settings.openserp_base_url}/bing/image"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, params={"text": query, "limit": count})
        resp.raise_for_status()
        data = resp.json()
    results = []
    for item in data.get("results", []):
        img = item.get("image", {})
        if img.get("url"):
            results.append({
                "title": item.get("title", ""),
                "image_url": img["url"],
                "source": item.get("source", ""),
                "engine": item.get("engine", ""),
            })
    return results


async def _search_doubao_ark(query: str, count: int) -> list:
    # 预留：豆包方舟多模态搜图
    raise NotImplementedError("doubao_ark provider 待接入")


async def _search_bing_api(query: str, count: int) -> list:
    # 预留：必应官方图片搜索 API
    raise NotImplementedError("bing_api provider 待接入")
