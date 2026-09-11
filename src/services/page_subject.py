"""分页画面主体提取（2026-08-31 移植 qvp-dev 8002，其源于用户反馈「图文不对应」）。

无参考图模式下生图模型要自己从分页文案解读画面主体，抽象文案就漂移（8002
实测：冰牛奶做法的封面被画成石头堆、尾页画成客厅）。asset_gen 生 6 页图前用
一次 LLM 调用从 6 页分页文案提取每页的具体画面主体，注入生图提示词替换通用
主体锚定条款（见 prompt_versions.get_image_prompt 的 page_subject 参数）。
解析失败/数量不等于 6/LLM 失败 → 返回 None，不阻塞出图（沿用通用锚定条款）。
"""
import json
import traceback

# 提取模板已搬入 skills/page-subject/SKILL.md（2026-09-03 阶段2重构，原样搬运）
from src.gateway.skill_loader import skill_body as _skill_body

_SUBJECT_PROMPT = _skill_body("page-subject")


async def extract_page_subjects(page_bodies: list, llm_call=None):
    """从 6 页分页文案提取每页画面主体，返回 6 个字符串的 list；失败返回 None。

    llm_call：文本模型调用入口（async，返回 dict 含 text），由调用方注入以便
    测试 mock；缺省走 failover 主备通道（单次重试，不拖慢出图链路）。
    """
    if llm_call is None:
        from src.gateway.failover import call_with_failover

        async def llm_call(prompt):
            return await call_with_failover(prompt, max_retries=1)
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
