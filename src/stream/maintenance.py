"""工作周期管理：工作 N 小时 → 检修停机 M 小时 → 循环。

停机期间调度器暂停取新任务，供后台导出记录 / 删除内容 / 重新开启。
"""
import asyncio
import time

from src.config import settings
from src.stream.bus import bus
from src.stream.scheduler import scheduler


class CycleManager:
    def __init__(self):
        self.mode = "working"            # working | maintenance
        self.reason = "init"
        self.cycle_started_at = time.time()
        self._monitor_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    @property
    def work_seconds(self) -> float:
        return settings.work_hours * 3600

    @property
    def maintenance_seconds(self) -> float:
        return settings.maintenance_hours * 3600

    async def start(self) -> None:
        if self._monitor_task is None:
            self._monitor_task = asyncio.create_task(self._monitor())

    async def stop(self) -> None:
        if self._monitor_task:
            self._monitor_task.cancel()
            self._monitor_task = None

    async def _monitor(self) -> None:
        while True:
            await asyncio.sleep(5)
            elapsed = time.time() - self.cycle_started_at
            if self.mode == "working" and elapsed >= self.work_seconds:
                await self.enter_maintenance("auto")
            elif self.mode == "maintenance" and elapsed >= self.maintenance_seconds:
                await self.exit_maintenance("auto")

    async def enter_maintenance(self, reason: str = "manual") -> dict:
        async with self._lock:
            if self.mode == "maintenance":
                return self.snapshot()
            self.mode = "maintenance"
            self.reason = reason
            self.cycle_started_at = time.time()
            scheduler.pause()
            await bus.publish("maintenance", {"mode": "maintenance", "reason": reason})
            return self.snapshot()

    async def exit_maintenance(self, reason: str = "manual") -> dict:
        async with self._lock:
            if self.mode == "working":
                return self.snapshot()
            self.mode = "working"
            self.reason = reason
            self.cycle_started_at = time.time()
            scheduler.resume()
            await bus.publish("maintenance", {"mode": "working", "reason": reason})
            return self.snapshot()

    def snapshot(self) -> dict:
        elapsed = time.time() - self.cycle_started_at
        if self.mode == "working":
            total = self.work_seconds
            next_event = "maintenance"
        else:
            total = self.maintenance_seconds
            next_event = "work"
        return {
            "mode": self.mode,
            "reason": self.reason,
            "elapsed_seconds": int(elapsed),
            "remaining_seconds": max(0, int(total - elapsed)),
            "next_event": next_event,
            "work_hours": settings.work_hours,
            "maintenance_hours": settings.maintenance_hours,
        }


cycle = CycleManager()
