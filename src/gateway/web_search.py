import re
import litellm
from src.config import settings


async def web_search(query: str, count: int = 6) -> list:
    """网页搜索（证据包），返回结构化结果 list[dict]（title/url/summary）。

    支持 provider 切换：doubao（结构化来源，默认）/ deepseek（联网总结）/ kimi（联网搜索）。
    """
    provider = settings.web_search_provider
    if provider == "doubao":
        return await _search_doubao(query, count)
    if provider == "deepseek":
        return await _search_deepseek(query)
    if provider == "kimi":
        return await _search_kimi(query, count)
    raise ValueError(f"unknown web search provider: {provider}")


async def deepseek_verify(query: str) -> tuple:
    """DeepSeek 联网搜索独立验证，返回 (总结文本, 成本元)（用于与豆包结构化来源交叉校验）。"""
    from src.gateway.cost_tracker import estimate_cost
    from src.gateway.failover import DEEPSEEK_MODEL
    r = await litellm.aresponses(
        model=DEEPSEEK_MODEL, input=query,
        tools=[{"type": "web_search"}], api_key=settings.deepseek_api_key)
    text = r.output_text if hasattr(r, "output_text") else str(r)
    cost = 0.0
    usage = getattr(r, "usage", None)
    if usage:
        cost = estimate_cost(DEEPSEEK_MODEL,
                             getattr(usage, "prompt_tokens", 0) or 0,
                             getattr(usage, "completion_tokens", 0) or 0)
    return text, cost


def _extract_founded_year(text: str):
    """提取创办/成立年份（针对学校/机构/产品类内容）。"""
    m = re.search(r"(?:成立于|创办于|创建于|建校于|建立于|始建)\s*(\d{4})\s*年?", text)
    return f"{m.group(1)}年" if m else None


def detect_conflict(source_summaries: list, deepseek_text: str) -> list:
    """比较豆包结构化来源 vs DeepSeek 结论的创办/成立年份，返回冲突列表。

    规则：两边都给出了创办年份，但值不一致 → 争议（如豆包 1990 vs DeepSeek 1981）。
    """
    source_year = None
    for s in source_summaries:
        y = _extract_founded_year(s)
        if y:
            source_year = y
            break
    deepseek_year = _extract_founded_year(deepseek_text)
    if source_year and deepseek_year and source_year != deepseek_year:
        return [f"创办年份不一致: 豆包来源 {source_year} vs DeepSeek {deepseek_year}"]
    return []


async def _search_doubao(query: str, count: int) -> list:
    url = "https://open.feedcoopapi.com/search_api/web_search"
    body = {
        "Query": query, "SearchType": "web", "Count": count,
        "Filter": {"NeedContent": False, "NeedUrl": True}, "NeedSummary": True,
    }
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {settings.doubao_search_key}"}
    from src.gateway.http_client import get_client
    # 共享 client（与 image_search 同 timeout 一组，P1-7）
    resp = await get_client("search", timeout=30).post(url, json=body, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    results = []
    for item in data.get("Result", {}).get("WebResults", []):
        results.append({
            "title": item.get("Title", ""),
            "url": item.get("Url", ""),
            "summary": item.get("Summary", item.get("Snippet", "")),
        })
    return results


async def _search_deepseek(query: str) -> list:
    text = await deepseek_verify(query)
    return [{"title": "deepseek-web-search", "url": "", "summary": text}]


async def _search_kimi(query: str, count: int = 6) -> list:
    """Kimi 联网搜索：优先调用新 /v1/tools/search 接口；失败则返回空列表。

    接口文档：https://platform.kimi.com/docs/guide/use-web-search
    每次搜索额外收取工具调用费（由 settings.kimi_search_cost_per_call 记录）。
    """
    import logging
    logger = logging.getLogger("src.gateway.web_search")
    url = "https://api.moonshot.cn/v1/tools/search"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.kimi_api_key}",
    }
    body = {"text_query": query, "limit": max(1, min(int(count), 20)),
            "include_content": False}
    try:
        from src.gateway.http_client import get_client
        resp = await get_client("search", timeout=30).post(url, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        logger.warning("kimi search failed: %s", e)
        return []

    candidates = data.get("search_results") or []
    if not isinstance(candidates, list):
        candidates = []
    results = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        title = item.get("title") or ""
        link = item.get("url") or ""
        summary = item.get("snippet") or ""
        results.append({"title": str(title), "url": str(link), "summary": str(summary)})
    return results
