"""自适应并发限制器（AIMD：加性增、乘性减，统计复用）。

根据任务成败与限流信号动态调整并发上限，让多路 worker 共享的总容量
随上游（LLM/生图/搜索）实际承载力弹性伸缩：
- 触发限流（429/rate limit）→ 并发减半（乘性减），下限 min_c；
  之后进入冷却期（cooldown_seconds），期内只允许成功计数、不允许升容，
  避免「刚被限流又立刻加压」的振荡
- 连续成功满一个周期且过了冷却期 → 并发 +1（加性增），上限 max_c
- 连续普通失败 ≥3 → 并发 -1
- 节点级限流信号（report_throttle）即时生效，不必等整条任务结束
"""
import asyncio
import time

from src.stream.bus import bus


class AdaptiveLimiter:
    def __init__(self, min_c: int = 1, max_c: int = 8, initial: int = 2,
                 cooldown_seconds: float = 120.0):
        self.min_c = min_c
        self.max_c = max_c
        self.capacity = max(min_c, min(initial, max_c))
        self.cooldown_seconds = cooldown_seconds
        self._in_flight = 0
        self._cond = asyncio.Condition()
        self._consecutive_success = 0
        self._consecutive_fail = 0
        self.last_throttled_at: float | None = None

    def _in_cooldown(self) -> bool:
        return (self.last_throttled_at is not None
                and time.time() - self.last_throttled_at < self.cooldown_seconds)

    async def acquire(self) -> None:
        async with self._cond:
            while self._in_flight >= self.capacity:
                await self._cond.wait()
            self._in_flight += 1

    async def release(self) -> None:
        async with self._cond:
            self._in_flight -= 1
            self._cond.notify_all()

    async def report(self, success: bool, throttled: bool) -> None:
        async with self._cond:
            old = self.capacity
            if throttled:
                self.capacity = max(self.min_c, self.capacity // 2)
                self._consecutive_success = 0
                self._consecutive_fail += 1
                self.last_throttled_at = time.time()
            elif success:
                self._consecutive_success += 1
                self._consecutive_fail = 0
                # 冷却期内只积累成功数，不升容（防抖）
                if not self._in_cooldown() \
                        and self._consecutive_success >= max(2, self.capacity * 2) \
                        and self.capacity < self.max_c:
                    self.capacity += 1
                    self._consecutive_success = 0
            else:
                self._consecutive_fail += 1
                self._consecutive_success = 0
                if self._consecutive_fail >= 3 and self.capacity > self.min_c:
                    self.capacity -= 1
                    self._consecutive_fail = 0

            changed = self.capacity != old
            if changed:
                self._cond.notify_all()

        if throttled:
            await bus.publish("rate_limit", {
                "capacity": self.capacity,
                "in_flight": self._in_flight,
            })
        if changed:
            await bus.publish("concurrency", {
                "capacity": self.capacity,
                "in_flight": self._in_flight,
                "previous": old,
            })

    async def report_throttle(self) -> None:
        """节点级限流即时上报（LLM/生图/搜索 429）：立即乘性减，不等任务收尾。"""
        await self.report(success=False, throttled=True)

    def snapshot(self) -> dict:
        return {
            "capacity": self.capacity,
            "in_flight": self._in_flight,
            "min_c": self.min_c,
            "max_c": self.max_c,
            "consecutive_success": self._consecutive_success,
            "consecutive_fail": self._consecutive_fail,
            "cooldown": self._in_cooldown(),
        }
