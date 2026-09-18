"""ESP-only capture timeline for the W1 wireless envelope; no PC clock mapping."""

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class WirelessRecord:
    boot_id: int
    captured_us: int
    prepared_us: int
    bridge_drops: int
    telemetry: str

    @classmethod
    def parse(cls, line: str) -> "WirelessRecord":
        parts = line.strip().split(",", 5)
        if len(parts) != 6 or parts[0] != "W1" or not parts[5].startswith("T1,"):
            raise ValueError("TCP requires W1 timestamped telemetry; update the ESP bridge firmware")
        if any(not part.isascii() or not part.isdecimal() for part in parts[1:5]):
            raise ValueError("W1 metadata must contain unsigned decimal integers")
        boot, captured, prepared, drops = map(int, parts[1:5])
        if not (boot <= 0xFFFFFFFF and drops <= 0xFFFFFFFF and
                0 <= captured <= prepared <= 0x7FFFFFFFFFFFFFFF):
            raise ValueError("W1 metadata is outside its range or precedes capture")
        return cls(boot, captured, prepared, drops, parts[5])


class WirelessTimeline:
    def __init__(self) -> None:
        self.boot_id: int | None = None
        self.origin_us = 0
        self.last_us: int | None = None
        self.capture_times: deque[int] = deque(maxlen=4096)
        self.bridge_drops = 0
        self.queue_age_ms = 0.0

    def append(self, record: WirelessRecord) -> tuple[float, bool]:
        """Return elapsed ESP seconds and whether the ESP boot changed."""
        rebooted = self.boot_id is not None and self.boot_id != record.boot_id
        if self.boot_id != record.boot_id:
            self.boot_id = record.boot_id
            self.origin_us = record.captured_us
            self.last_us = None
            self.capture_times.clear()
        if self.last_us is not None and record.captured_us < self.last_us:
            raise ValueError("W1 capture time moved backwards within one ESP boot")
        self.last_us = record.captured_us
        self.capture_times.append(record.captured_us)
        while len(self.capture_times) > 2 and self.capture_times[0] < record.captured_us - 1_000_000:
            self.capture_times.popleft()
        self.bridge_drops = record.bridge_drops
        self.queue_age_ms = (record.prepared_us - record.captured_us) / 1000
        return (record.captured_us - self.origin_us) / 1_000_000, rebooted

    @property
    def sample_hz(self) -> float:
        if len(self.capture_times) < 2:
            return 0.0
        duration = self.capture_times[-1] - self.capture_times[0]
        return (len(self.capture_times) - 1) * 1_000_000 / duration if duration else 0.0
