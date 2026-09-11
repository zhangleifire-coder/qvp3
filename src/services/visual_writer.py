"""场景化扩写器（2026-09-01）：把 6 页中文文案扩写为英文视觉描述，喂给 gpt-image-2。

背景：ChatGPT 网页生图质量高于裸 API 的核心在于网页有一层对话 Agent 把口语
prompt 改写成丰富视觉描述。我们复刻这一层，但只扩「画面视觉」，不动确定性
约束骨架（字数/汉字正确性/六页统一由英文约束底座保底）。

记忆机制（用户要求「利用 nanobot 的记忆技术持续迭代」）：
- 固定 nanobot 会话 qvp-visual-memory-v1：历次扩写的风格取向自然沉淀在会话
  上下文里，越用越贴合团队口味；
- 每次调用注入「当前风格库描述」（曾经训练的要求）与「近期审图反馈笔记」
  （驳回意见=未来训练），nanobot 会话记忆 + 显式注入双保险；
- note_to_memory()：审图驳回时向同一会话追加优化笔记（fire-and-forget）。

链路：nanobot 记忆会话优先（90s 超时）→ DeepSeek/Kimi failover 回退（无跨任务
记忆，但单任务 6 页一次产出仍统一）。两级失败返回 None，调用方走中文回退骨架。
"""
import asyncio
import json
import traceback

VISUAL_SESSION = "qvp-visual-memory-v1"

# 扩写模板已搬入 skills/visual-writer/SKILL.md（2026-09-03 阶段2重构，原样搬运；
# {style_name}/{style_desc}/{notes}/{pages} 由 _build_message 以 replace 填充）
from src.gateway.skill_loader import skill_body as _skill_body

_VISUAL_PROMPT = _skill_body("visual-writer")


def _build_message(style_name: str, style_desc: str, page_bodies: list,
                   notes: list[str]) -> str:
    pages = "\n".join(f"Page {i}：{b}" for i, b in enumerate(page_bodies, 1))
    notes_txt = "\n".join(f"- {n}" for n in notes[:6]) if notes else "（none yet）"
    return (_VISUAL_PROMPT
            .replace("{style_name}", style_name or "")
            .replace("{style_desc}", style_desc or "")
            .replace("{notes}", notes_txt)
            .replace("{pages}", pages))


def _parse_visual(text: str):
    """解析 {"style_en":..., "pages":[6]}；任何不合格返回 None。"""
    try:
        raw = (text or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`").lstrip("json").strip()
        obj = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
        style_en = str(obj.get("style_en") or "").strip()
        pages = [str(p).strip() for p in (obj.get("pages") or [])]
        if len(pages) != 6 or not all(pages) or not style_en:
            return None
        return {"style_en": style_en, "pages": pages}
    except Exception:
        return None


async def write_page_visuals(style_name: str, style_desc: str,
                             page_bodies: list, notes: list[str] = None) -> dict | None:
    """6 页中文文案 → {"style_en", "pages":[6 条英文视觉描述]}；失败 None。

    优先 nanobot 固定记忆会话（跨任务持续迭代），90s 超时/失败回退
    call_with_failover（DeepSeek 主/Kimi 备）。任何一级成功即返回。
    """
    bodies = list(page_bodies or [])[:6]
    while len(bodies) < 6:
        bodies.append("")
    msg = _build_message(style_name, style_desc, bodies, notes or [])

    # ── 一级：nanobot 记忆会话（上下文沉淀历次扩写与优化笔记）──
    try:
        from src.gateway.nanobot_client import call_agent, NanobotUnavailableError
        try:
            r = await asyncio.wait_for(
                call_agent(msg, session_id=VISUAL_SESSION), timeout=90.0)
            parsed = _parse_visual(r.get("text") or "")
            if parsed:
                return parsed
        except (asyncio.TimeoutError, NanobotUnavailableError):
            pass  # nanobot 不可达/超时 → 直接走文本回退
        except Exception:
            traceback.print_exc()  # 解析/其它异常也回退
    except Exception:
        traceback.print_exc()

    # ── 二级：DeepSeek/Kimi failover（无跨任务记忆）──
    try:
        from src.gateway.failover import call_with_failover
        r = await call_with_failover(msg, max_retries=1)
        return _parse_visual(r.get("text") or "")
    except Exception:
        traceback.print_exc()
        return None


async def note_to_memory(note: str) -> None:
    """把人工反馈/优化笔记追加进视觉扩写记忆会话（下一次扩写会带上）。

    fire-and-forget：任何失败只打印，绝不阻塞调用方（审图驳回主流程）。
    """
    note = (note or "").strip()
    if not note:
        return
    try:
        from src.gateway.nanobot_client import call_agent
        await asyncio.wait_for(
            call_agent(
                "Remember this reviewer feedback for future visual directions "
                "(do not output anything else, just acknowledge briefly):\n"
                + note[:500],
                session_id=VISUAL_SESSION),
            timeout=60.0)
    except Exception:
        traceback.print_exc()
