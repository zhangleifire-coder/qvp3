"""task_id 级工具配额：MCP 进程内计数，超限抛错。

LLM 不自律是常态，花钱的口子必须在工具侧焊死：
- 生图 ¥0.2/张：默认每任务 ≤8 张（6 张交付 + 2 张去重重生余量）
- 网页搜索：默认每任务 ≤3 次
- OCR：默认每任务 ≤8 张
上限经 src.config.settings 读取，与后端共用同一份 .env。
"""
import threading

from src.config import settings


class QuotaExceededError(RuntimeError):
    pass


class _TaskQuotas:
    def __init__(self):
        self._lock = threading.Lock()
        self._used: dict[str, dict[str, int]] = {}

    def _task(self, task_id: str) -> dict:
        return self._used.setdefault(str(task_id), {})

    def check_and_consume(self, task_id: str, kind: str, n: int = 1) -> None:
        """占用 n 个配额，超限抛 QuotaExceededError（Agent 会看到错误并收敛）。"""
        limit = {
            "image": settings.mcp_max_images_per_task,
            "web_search": settings.mcp_max_web_searches_per_task,
            "image_search": settings.mcp_max_image_searches_per_task,
            "ocr": settings.mcp_max_ocr_per_task,
        }[kind]
        with self._lock:
            t = self._task(task_id)
            used = t.get(kind, 0)
            if used + n > limit:
                raise QuotaExceededError(
                    f"任务 {task_id} 的 {kind} 配额已用尽（上限 {limit}，已用 {used}）。"
                    f"请停止继续调用该工具，用现有结果继续完成任务。")
            t[kind] = used + n

    def release(self, task_id: str) -> None:
        """任务结束后清掉计数（后端可经管理端触发；进程内自动过期策略后续再加）。"""
        with self._lock:
            self._used.pop(str(task_id), None)


quotas = _TaskQuotas()
