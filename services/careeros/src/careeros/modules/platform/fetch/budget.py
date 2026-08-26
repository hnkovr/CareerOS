"""Bounds of one job read: attempts, wall time, archive and search calls (spec §5.3)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from careeros.core.config import Settings
from careeros.modules.platform.enums import ARCHIVE_STRATEGIES, SEARCH_STRATEGIES, FetchStrategy


@dataclass(frozen=True, slots=True)
class BudgetRemaining:
    attempts: int
    seconds: float
    archive_calls: int
    search_calls: int


@dataclass(slots=True)
class FetchBudget:
    max_attempts: int = 4
    max_total_s: float = 30.0
    max_archive_calls: int = 2
    max_search_calls: int = 0
    clock: Callable[[], float] = time.monotonic
    started_at: float | None = None
    attempts_used: int = 0
    archive_calls_used: int = 0
    search_calls_used: int = 0

    @classmethod
    def from_settings(cls, settings: Settings) -> FetchBudget:
        return cls(
            max_attempts=settings.job_fetch_max_attempts,
            max_total_s=settings.job_fetch_max_total_s,
            max_archive_calls=settings.job_fetch_max_archive_calls,
            max_search_calls=settings.job_fetch_max_search_calls,
        )

    def start(self) -> None:
        if self.started_at is None:
            self.started_at = self.clock()

    def elapsed_s(self) -> float:
        if self.started_at is None:
            return 0.0
        return max(0.0, self.clock() - self.started_at)

    def remaining(self) -> BudgetRemaining:
        return BudgetRemaining(
            attempts=max(0, self.max_attempts - self.attempts_used),
            seconds=max(0.0, self.max_total_s - self.elapsed_s()),
            archive_calls=max(0, self.max_archive_calls - self.archive_calls_used),
            search_calls=max(0, self.max_search_calls - self.search_calls_used),
        )

    def allows(self, strategy: FetchStrategy) -> str | None:
        """``None`` when ``strategy`` may run now, else the reason it may not."""
        left = self.remaining()
        if left.attempts <= 0:
            return f"attempts exhausted ({self.max_attempts})"
        if left.seconds <= 0:
            return f"time budget exhausted ({self.max_total_s:g}s)"
        if strategy in ARCHIVE_STRATEGIES and left.archive_calls <= 0:
            return f"archive calls exhausted ({self.max_archive_calls})"
        if strategy in SEARCH_STRATEGIES and left.search_calls <= 0:
            return f"search calls exhausted ({self.max_search_calls})"
        return None

    def consume(self, strategy: FetchStrategy) -> None:
        self.start()
        self.attempts_used += 1
        if strategy in ARCHIVE_STRATEGIES:
            self.archive_calls_used += 1
        if strategy in SEARCH_STRATEGIES:
            self.search_calls_used += 1

    def exhausted(self) -> bool:
        left = self.remaining()
        return left.attempts <= 0 or left.seconds <= 0
