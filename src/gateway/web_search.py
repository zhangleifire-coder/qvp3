import re
import httpx
import litellm
from src.config import settings


async def web_search(query: str, count: int = 6) -> list:
    """网页搜索（证据包），返回结构化结果 list[dict]（title/url/summary）。

    预留 provider 切换：doubao（结构化来源，默认）/ deepseek（联网总结）。
    """
    provider = settings.web_search_provider
    if provider == "doubao":
        return await _search_doubao(query, count)
    if provider == "deepseek":
        return await _search_deepseek(query)
    raise ValueError(f"unknown web search provider: {provider}")


async def deepseek_verify(query: str) -> tuple:
    """DeepSeek 联网搜索独立验证，返回 (总结文本, 成本元)（用于与豆包结构化来源交叉校验）。"""
    from src.gateway.cost_tracker import estimate_cost
    r = await litellm.aresponses(
        model="deepseek/deepseek-v4-pro", input=query,
        tools=[{"type": "web_search"}], api_key=settings.deepseek_api_key)
    text = r.output_text if hasattr(r, "output_text") else str(r)
    cost = 0.0
    usage = getattr(r, "usage", None)
    if usage:
        cost = estimate_cost("deepseek/deepseek-v4-pro",
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
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=body, headers=headers)
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
