"""一次性训练脚本（2026-08-24）：把 37 篇新榜借鉴库的「图片丰富度」学习结论
写进视觉扩写记忆会话 qvp-visual-memory-v1，让后续每次场景化扩写都带着这套基准。

与代码注入（prompt_versions 底座 + visual_writer RICHNESS DEVICES 段）互为双保险：
代码注入保证下限，会话记忆让扩写器在自由发挥时也向这套基准靠拢。

用法：本地/服务器容器内执行 .venv python scripts/seed_richness_memory.py
幂等性：重复执行只是重复追加同一条笔记，无副作用（会话上下文可承受）。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.services.visual_writer import note_to_memory  # noqa: E402

NOTE = (
    "TRAINING DIGEST (2026-08-24, from 37 top-performing reference notes — "
    "the 'richness' benchmark). Apply in all future visual directions: "
    "(1) every inner page pairs its headline with a slim saturated dark "
    "banner bar carrying a one-line white sub-slogan; (2) bullets get "
    "circular numbered badges; (3) inset photos sit in rounded/torn-paper "
    "frames and may carry a small round verdict sticker; (4) wrap-up pages "
    "use a green-check vs red-cross two-column quick check or a dark "
    "conclusion banner; (5) keyword highlight = light accent-color block "
    "with dark text, highlighter feel; (6) cover headlines are very large, "
    "up to ~80% page width; (7) inner pages carry 60-100 Chinese characters, "
    "balanced across pages; (8) one accent color per set, tied to topic "
    "(orange=lifestyle, blue=digital, red=complaints/danger, vermilion="
    "history, green=garden, gold=finance); light background + dark ink + "
    "single accent. Richness yes, clutter no — magazine restraint."
)


async def main():
    from src.gateway.nanobot_client import call_agent, NanobotUnavailableError
    try:
        await asyncio.wait_for(note_to_memory(NOTE), timeout=70.0)
        # note_to_memory 吞掉异常，这里验证会话真的收到了
        from src.services.visual_writer import VISUAL_SESSION
        r = await asyncio.wait_for(
            call_agent("Reply with exactly: TRAINING OK", session_id=VISUAL_SESSION),
            timeout=60.0)
        print("[seed] memory session replied:", (r.get("text") or "")[:80])
    except (asyncio.TimeoutError, NanobotUnavailableError) as e:
        print(f"[seed] nanobot 不可达，记忆未写入（{e}）。"
              f"请在 nanobot 可用的环境重跑本脚本。")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
