"""The kill switch. Plain code, never a model: any breached threshold stops the fault and restores the services."""
from __future__ import annotations

from dataclasses import asdict, dataclass

MIN_SAMPLES = 5  # do not judge an error rate on fewer completed units than this


@dataclass(frozen=True)
class Thresholds:
    error_rate: float = 0.6  # over the last 3 s
    p95_ms: float = 3000.0
    unhealthy: int = 3  # blast radius: services not running or unhealthy
    max_wall_s: float = 45.0


class Guard:
    def __init__(self, thresholds: Thresholds | None = None):
        self.thresholds = thresholds or Thresholds()
        self.armed = False
        self.tripped = False
        self.reason: str | None = None

    def arm(self) -> None:
        self.armed, self.tripped, self.reason = True, False, None

    def disarm(self) -> None:
        self.armed = False

    def check(self, *, error_rate: float, completed: int, p95_ms: float, unhealthy: int, wall_s: float) -> str | None:
        """Returns the trip reason the first time a threshold is breached, else None."""
        if not self.armed or self.tripped:
            return None
        th = self.thresholds
        reason = None
        if completed >= MIN_SAMPLES and error_rate >= th.error_rate:
            reason = f"Error rate {error_rate:.0%} over the last 3 s reached the {th.error_rate:.0%} limit"
        elif p95_ms >= th.p95_ms:
            reason = f"p95 latency {p95_ms / 1000:.1f} s reached the {th.p95_ms / 1000:.1f} s limit"
        elif unhealthy >= th.unhealthy:
            reason = f"{unhealthy} services unhealthy reached the blast-radius limit of {th.unhealthy}"
        elif wall_s > th.max_wall_s:
            reason = f"Experiment ran {wall_s:.0f} s, over the {th.max_wall_s:.0f} s limit"
        if reason:
            self.tripped, self.reason = True, reason
        return reason

    def view(self, seconds_left: float) -> dict:
        return {"armed": self.armed, "tripped": self.tripped, "reason": self.reason,
                "thresholds": asdict(self.thresholds), "seconds_left": round(max(0.0, seconds_left), 1)}
