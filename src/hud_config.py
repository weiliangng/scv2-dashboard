"""HUD scales; firmware references are documented in docs/HUD.md."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class HudConfig:
    bank_capacitance_f: float = 5.0  # Firmware estimate, not measured capacitance.
    cap_ceiling_v: float = 26.3
    cap_cutoff_v: float = 26.3 * 0.20
    power_full_scale_w: float = 240.0
    current_full_scale_a: float = 15.0  # OUT limit; IN's 10 A is a different port.
    power_overlay_span_ratio: float = 0.30
    average_samples: int = 20
    stale_after_s: float = 1.0
    energy_height: int = 36
    current_height: int = 10
    padding: int = 24
    energy_color: str = "#F5CE42"
    charge_color: str = "#43D58D"
    discharge_color: str = "#EC6570"

    def __post_init__(self) -> None:
        for value in (self.bank_capacitance_f, self.cap_ceiling_v,
                      self.power_full_scale_w, self.current_full_scale_a,
                      self.power_overlay_span_ratio, self.stale_after_s, self.padding):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("HUD scales must be finite and positive")
        if not 0 <= self.cap_cutoff_v < self.cap_ceiling_v:
            raise ValueError("Invalid capacitor voltage window")
        if not isinstance(self.average_samples, int) or self.average_samples <= 0:
            raise ValueError("HUD average_samples must be a positive integer")
        if self.power_overlay_span_ratio > 1 or not 0 < self.current_height < self.energy_height:
            raise ValueError("Invalid HUD geometry")

    @property
    def capacity_j(self) -> float:
        return 0.5 * self.bank_capacitance_f * (self.cap_ceiling_v**2 - self.cap_cutoff_v**2)


HUD_CONFIG = HudConfig()
