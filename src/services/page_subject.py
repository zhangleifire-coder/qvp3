"""分页画面主体提取（2026-08-31 移植 qvp-dev 8002，其源于用户反馈「图文不对应」）。

无参考图模式下生图模型要自己从分页文案解读画面主体，抽象文案就漂移（8002
实测：冰牛奶做法的封面被画成石头堆、尾页画成客厅）。asset_gen 生 6 页图前用
一次 LLM 调用从 6 页分页文案提取每页的具体画面主体，注入生图提示词替换通用
主体锚定条款（见 prompt_versions.get_image_prompt 的 page_subject 参数）。
解析失败/数量不等于 6/LLM 失败 → 返回 None，不阻塞出图（沿用通用锚定条款）。
"""
import json
import traceback

_SUBJECT_PROMPT = """你是小红书图文的视觉总监。下面是同一套图文的 6 页图上文案，请为每页提取一个「画面主体」：本页配图应该直接画出的具体事物。
要求：
1. 输出严格的 JSON 数组，恰好 6 个字符串，与 6 页一一对应，不要输出任何其他文字、解释或 markdown 代码围栏。
2. 每个主体不超过 20 字，必须是具体可画的名词短语（如「加冰块的高脚杯牛奶」「两只碰杯的手特写」）。
3. 禁止抽象词（如「坚韧」「温馨」「成长」），禁止与文案无关的象征隐喻物；文案讲什么就画什么。

【6 页文案】
{pages}"""


async def extract_page_subjects(page_bodies: list, llm_call=None):
    """从 6 页分页文案提取每页画面主体，返回 6 个字符串的 list；失败返回 None。

    llm_call：文本模型调用入口（async，返回 dict 含 text），由调用方注入以便
    测试 mock；缺省走 failover 主备通道（单次重试，不拖慢出图链路）。
    """
    if llm_call is None:
        from src.gateway.failover import call_with_failover, DEEPSEEK_MODEL, KIMI_MODEL

        async def llm_call(prompt):
            return await call_with_failover(prompt, DEEPSEEK_MODEL, KIMI_MODEL,
                                            max_retries=1)
    try:
        pages = "\n".join(f"第{i}页：{b}" for i, b in enumerate(page_bodies, 1))
        r = await llm_call(_SUBJECT_PROMPT.replace("{pages}", pages))
        raw = (r.get("text") or r.get("content") or "").strip()
        arr = json.loads(raw[raw.index("["):raw.rindex("]") + 1])
        arr = [str(s).strip() for s in arr]
        if len(arr) != 6 or not all(arr):
            return None
        return arr
    except Exception:
        traceback.print_exc()  # 提取失败不阻塞出图，沿用通用锚定条款
        return None
