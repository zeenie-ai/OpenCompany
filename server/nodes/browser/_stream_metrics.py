"""Opt-in, bounded live-view diagnostics. Never accepts page or input payloads."""

from __future__ import annotations

import os
import time
from collections import Counter, defaultdict, deque

from core.logging import get_logger

logger = get_logger(__name__)


class StreamMetrics:
    def __init__(self) -> None:
        self.enabled = os.environ.get("OPENCOMPANY_BROWSER_DIAGNOSTICS") == "1"
        self.samples: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=256))
        self.counts: Counter[str] = Counter()
        self._reported = time.monotonic()

    def observe(self, name: str, seconds: float) -> None:
        if self.enabled:
            self.samples[name].append(max(0.0, seconds) * 1000)
            self._report()

    def count(self, name: str) -> None:
        if self.enabled:
            self.counts[name] += 1
            self._report()

    def snapshot(self) -> dict:
        timings = {}
        for name, samples in self.samples.items():
            values = sorted(samples)
            if values:
                timings[name] = {"n": len(values), "p50_ms": round(values[(len(values) - 1) // 2], 2), "p95_ms": round(values[min(len(values) - 1, int(len(values) * .95))], 2)}
        return {"timings": timings, "counts": dict(self.counts)}

    def _report(self) -> None:
        now = time.monotonic()
        if now - self._reported >= 5:
            self._reported = now
            logger.info("[browser] live-view timings %s", self.snapshot())
