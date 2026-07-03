"""自适应采集间隔（T058，AIMD）。

限流友好的加性增/乘性减间隔控制器：
- 成功 → 加性靠拢目标间隔（逐步提速到 target，不激进）。
- 限流 → 乘性退避（×2 拉大间隔），上限 max_sec（≤10min，contracts/ingestion.md）。
纯逻辑、可注入、可单测（宪章 IV）；无 I/O、无时钟。
"""

from __future__ import annotations


class AdaptiveInterval:
    """AIMD 间隔控制器。current 为下次采集应等待的秒数。"""

    def __init__(
        self, *, target_sec: int, min_sec: int, max_sec: int,
        additive_step_sec: int = 30, backoff_factor: float = 2.0,
    ) -> None:
        if not (min_sec <= target_sec <= max_sec):
            raise ValueError(f"需 min<=target<=max，得 {min_sec}/{target_sec}/{max_sec}")
        self._target = target_sec
        self._min = min_sec
        self._max = max_sec
        self._step = additive_step_sec
        self._factor = backoff_factor
        self._current = float(target_sec)

    @property
    def current(self) -> int:
        return int(round(self._current))

    def on_success(self) -> int:
        """成功：加性向 target 靠拢（若已被退避到 target 之上，逐步降回）。"""
        if self._current > self._target:
            self._current = max(self._target, self._current - self._step)
        else:
            self._current = self._target  # 已达目标，稳定在 target
        self._clamp()
        return self.current

    def on_rate_limited(self) -> int:
        """限流：乘性退避，封顶 max_sec。"""
        self._current = min(self._max, self._current * self._factor)
        self._clamp()
        return self.current

    def reset(self) -> None:
        self._current = float(self._target)

    def _clamp(self) -> None:
        self._current = max(self._min, min(self._max, self._current))
