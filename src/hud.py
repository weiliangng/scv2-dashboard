"""Voltage-derived energy/potential power and independent measured-current HUD."""

from collections import deque
from dataclasses import dataclass
import time

from PySide6 import QtCore, QtGui, QtWidgets

from hud_config import HUD_CONFIG, HudConfig


@dataclass(frozen=True)
class HudValues:
    voltage_v: float
    load_w: float
    current_a: float
    energy_j: float
    fraction: float
    budget_w: float | None
    budget_source: str
    potential_w: float | None
    status: int


class HudAverager:
    """Fixed-count boxcar average for the HUD's three analogue measurements."""

    def __init__(self, sample_count: int) -> None:
        if not isinstance(sample_count, int) or sample_count <= 0:
            raise ValueError("sample_count must be a positive integer")
        self.sample_count = sample_count
        self.samples: deque[tuple[float, float, float]] = deque()
        self.sums = [0.0, 0.0, 0.0]

    def reset(self) -> None:
        self.samples.clear()
        self.sums[:] = (0.0, 0.0, 0.0)

    def add(self, sample: dict[str, int]) -> tuple[float, float, float]:
        values = (sample["vc_mV"] / 1000.0,
                  sample["vb_mV"] * sample["il_mA"] / 1_000_000.0,
                  sample["io_mA"] / 1000.0)
        if len(self.samples) == self.sample_count:
            removed = self.samples.popleft()
            for index, value in enumerate(removed):
                self.sums[index] -= value
        self.samples.append(values)
        for index, value in enumerate(values):
            self.sums[index] += value
        return tuple(total / len(self.samples) for total in self.sums)


def hud_values(sample: dict[str, int], cfg: HudConfig = HUD_CONFIG,
               measurements: tuple[float, float, float] | None = None) -> HudValues:
    """Adapt T1 without claiming it contains the averaged 0x077 load sample."""
    voltage, load, current = measurements or (
        sample["vc_mV"] / 1000.0,
        sample["vb_mV"] * sample["il_mA"] / 1_000_000.0,
        sample["io_mA"] / 1000.0,
    )
    energy = min(cfg.capacity_j, max(0.0, 0.5 * cfg.bank_capacitance_f *
                                   (max(0.0, voltage)**2 - cfg.cap_cutoff_v**2)))
    # Follow EXTERNAL source arbitration, even when safety inhibits switching.
    # pset_W is intentionally not used: burst control can modify that setpoint.
    budget, source = None, "No fresh referee input"
    if sample["mode_req"] != 0:
        source = "Referee budget inactive in this mode"
    elif all(sample[k] for k in ("uart_p_valid", "uart_p_fresh", "uart_e_valid", "uart_e_fresh")):
        budget, source = float(sample["uart_p"]), "UART"
    elif sample["can_p_valid"] and sample["can_p_fresh"]:
        budget, source = float(sample["can_p"]), "CAN"
    status = (sample["fault_bits"] & 0x03) | (
        0x04 if sample["can_p_valid"] and sample["can_p_fresh"] else 0)
    return HudValues(voltage, load, current, energy,
                     energy / cfg.capacity_j, budget, source,
                     None if budget is None else budget - load, status)


def bar_geometry(width: float, fraction: float, power: float | None,
                 current: float, cfg: HudConfig = HUD_CONFIG) -> dict[str, float]:
    """Logical pixels including reserved overflow space at both energy ends."""
    span = width / (1 + 2 * cfg.power_overlay_span_ratio)
    origin = cfg.power_overlay_span_ratio * span
    tip = origin + max(0.0, min(1.0, fraction)) * span
    power = power or 0.0
    power_width = cfg.power_overlay_span_ratio * span * min(abs(power) / cfg.power_full_scale_w, 1)
    zero = origin + span / 2
    current_width = span / 2 * min(abs(current) / cfg.current_full_scale_a, 1)
    return dict(origin=origin, span=span, tip=tip, power_width=power_width,
                power_left=tip if power >= 0 else tip - power_width,
                zero=zero, current_width=current_width,
                current_left=zero if current >= 0 else zero - current_width)


class PowerBars(QtWidgets.QWidget):
    def __init__(self, cfg: HudConfig = HUD_CONFIG) -> None:
        super().__init__()
        self.cfg = cfg
        self.values: HudValues | None = None
        self.stale = False
        self.setMinimumHeight(154)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Capacitor energy, potential power, and measured converter current bars")
        self.current_label = QtWidgets.QLabel("Output — A", self)
        self.current_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.current_label.setStyleSheet("font-weight: 600;")

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.current_label.setGeometry(0, 78, self.width(), 26)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802
        del event
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        c, v = self.cfg, self.values
        p.translate(c.padding, 0)
        width = max(1.0, self.width() - 2 * c.padding)
        g = bar_geometry(width, v.fraction if v else 0, v.potential_w if v else None,
                         v.current_a if v else 0, c)
        text = self.palette().color(QtGui.QPalette.ColorRole.Text)
        track = self.palette().color(QtGui.QPalette.ColorRole.Mid)
        if self.stale:
            p.setOpacity(0.40)

        def rect(x: float, y: float, w: float, h: float, color: QtGui.QColor | str) -> None:
            p.fillRect(QtCore.QRectF(x, y, w, h), QtGui.QColor(color))

        def label(x: float, y: float, s: str) -> None:
            p.setPen(text)
            w = p.fontMetrics().horizontalAdvance(s) + 8
            p.drawText(QtCore.QRectF(x - w / 2, y, w, 22), QtCore.Qt.AlignmentFlag.AlignCenter, s)

        y = 12.0
        rect(g["origin"], y, g["span"], c.energy_height, track)
        if v:
            rect(g["origin"], y, g["tip"] - g["origin"], c.energy_height, c.energy_color)
            if v.potential_w is not None:
                rect(g["power_left"], y, g["power_width"], c.energy_height,
                     c.charge_color if v.potential_w >= 0 else c.discharge_color)
                if g["power_width"] > 22:
                    p.setPen(QtGui.QColor("#18222C"))
                    p.drawText(QtCore.QRectF(g["power_left"], y, g["power_width"], c.energy_height),
                               QtCore.Qt.AlignmentFlag.AlignCenter, "→" if v.potential_w >= 0 else "←")
            rect(g["tip"] - 1, y - 6, 2, c.energy_height + 12, text)
        ticks = (0, .25, .5, .75, 1) if width >= 500 else (0, 1)
        for fraction in ticks:
            x = g["origin"] + fraction * g["span"]
            if fraction in (0, 1):
                rect(x, y - 4, 1, c.energy_height + 8, text)
            label(x, y + c.energy_height + 8, f"{fraction:.0%}")
        y = 112.0
        rect(g["origin"], y, g["span"], c.current_height, track)
        if v:
            rect(g["current_left"], y, g["current_width"], c.current_height,
                 c.charge_color if v.current_a >= 0 else c.discharge_color)
        rect(g["zero"] - 1, y - 5, 2, c.current_height + 10, text)
        for x, s in ((g["origin"], f"−{c.current_full_scale_a:g} A"), (g["zero"], "0 A"),
                     (g["origin"] + g["span"], f"+{c.current_full_scale_a:g} A")):
            label(x, y + c.current_height + 9, s)
        p.end()


class HudPage(QtWidgets.QScrollArea):
    def __init__(self, demo: bool = False, cfg: HudConfig = HUD_CONFIG) -> None:
        super().__init__()
        self.cfg, self.demo = cfg, demo
        self.averager = HudAverager(cfg.average_samples)
        self.pending_values: HudValues | None = None
        self.received_at: float | None = None
        self.sample: dict[str, int] | None = None
        self.setWidgetResizable(True)
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        body = QtWidgets.QWidget()
        self.setWidget(body)
        layout = QtWidgets.QVBoxLayout(body)
        layout.setContentsMargins(cfg.padding, cfg.padding, cfg.padding, cfg.padding)
        layout.setSpacing(12)
        title = QtWidgets.QLabel("Chassis energy monitor")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        layout.addWidget(title)
        self.quality = QtWidgets.QLabel()
        layout.addWidget(self.quality)
        self.energy = QtWidgets.QLabel("— J · —%")
        self.energy.setStyleSheet("font-size: 28px; font-weight: 600;")
        self.potential = QtWidgets.QLabel("Potential unavailable")
        self.potential.setStyleSheet("font-size: 18px;")
        self.voltage = QtWidgets.QLabel("— V")
        for widget in (self.energy, self.voltage, self.potential):
            widget.setWordWrap(True)
            layout.addWidget(widget)
        self.bars = PowerBars(cfg)
        self.current = self.bars.current_label
        layout.addWidget(self.bars)
        self.notes = QtWidgets.QLabel()
        self.notes.setWordWrap(True)
        layout.addWidget(self.notes)
        self.boxes: dict[str, QtWidgets.QLineEdit] = {}
        self._add_fields(layout, "Referee inputs · received by SCV2", (
            ("can_p", "CAN power_limit"), ("can_e", "CAN energy_buffer"),
            ("uart_p", "UART power_limit"), ("uart_e", "UART energy_buffer"),
            ("budget", "Potential-power budget"), ("control", "Controller / switching")))
        self._add_fields(layout, f"0x077 telemetry quantities · {cfg.average_samples}-sample average", (
            ("load", "Chassis load (Vbus × Iload)"), ("voltage", "Capacitor voltage"),
            ("current", "Measured output current"), ("vbus_ovp", "Vbus OVP latch · bit 0"),
            ("vcap_ovp", "Vcap OVP latch · bit 1"), ("fresh", "CAN command fresh · bit 2"),
            ("status", "Equivalent status byte"), ("ceiling", "Runtime charge ceiling")))
        foot = QtWidgets.QLabel(
            "Yellow: estimated usable energy from voltage. Green/red: referee budget minus chassis load; "
            "this is potential power, not measured flow. The lower strip uses measured output current only.\n"
            "T1 supplies the measurements and status flags. CAN 0x077 uses an averaged, unsigned load value; "
            "the load shown here is calculated from T1. Byte 6 is reserved (zero).")
        foot.setWordWrap(True)
        layout.addWidget(foot)
        layout.addStretch(1)
        self.refresh_quality()

    def _add_fields(self, layout: QtWidgets.QVBoxLayout, title: str,
                    fields: tuple[tuple[str, str], ...]) -> None:
        group = QtWidgets.QGroupBox(title)
        form = QtWidgets.QFormLayout(group)
        form.setRowWrapPolicy(QtWidgets.QFormLayout.RowWrapPolicy.WrapLongRows)
        for key, caption in fields:
            box = QtWidgets.QLineEdit("—")
            box.setReadOnly(True)
            box.setAccessibleName(caption)
            self.boxes[key] = box
            form.addRow(caption, box)
        layout.addWidget(group)

    def reset(self) -> None:
        self.sample = None
        self.averager.reset()
        self.pending_values = None
        self.received_at = None
        self.bars.values = None
        self.energy.setText("— J · —%")
        self.voltage.setText("— V")
        self.potential.setText("Potential unavailable")
        self.current.setText("Output — A")
        self.notes.clear()
        for box in self.boxes.values():
            box.setText("—")
        self.refresh_quality()

    def accumulate_sample(self, sample: dict[str, int], received_at: float | None = None) -> None:
        """Add one valid T1 record without forcing an intermediate widget redraw."""
        self.sample = sample.copy()
        self.received_at = time.monotonic() if received_at is None else received_at
        self.pending_values = hud_values(sample, self.cfg, self.averager.add(sample))

    def set_sample(self, sample: dict[str, int], received_at: float | None = None,
                   already_accumulated: bool = False) -> None:
        if not already_accumulated:
            self.accumulate_sample(sample, received_at)
        elif self.pending_values is None:
            raise ValueError("No accumulated HUD sample is available")
        else:
            self.sample = sample.copy()
            self.received_at = time.monotonic() if received_at is None else received_at
        v = self.pending_values
        assert v is not None
        self.bars.values = v
        self.energy.setText(f"{v.energy_j:,.0f} J · {v.fraction:.1%}")
        self.voltage.setText(f"{v.voltage_v:.2f} V")
        arrow = "→" if v.current_a > 0 else "←" if v.current_a < 0 else ""
        self.current.setText(f"Output {abs(v.current_a):.2f} A {arrow}".rstrip())
        if v.potential_w is None:
            self.potential.setText(f"Potential unavailable · {v.budget_source}")
        else:
            arrow = "→" if v.potential_w > 0 else "←" if v.potential_w < 0 else ""
            self.potential.setText(f"{v.potential_w:+.1f} W {arrow}   |   Load {v.load_w:.1f} W")
        notes = []
        if not self.cfg.cap_cutoff_v <= v.voltage_v <= self.cfg.cap_ceiling_v:
            notes.append("Voltage outside nominal energy window; fill clamped")
        if v.potential_w is not None and abs(v.potential_w) > self.cfg.power_full_scale_w:
            notes.append("Potential power over display scale")
        if abs(v.current_a) > self.cfg.current_full_scale_a:
            notes.append("Measured current over ±15 A configured output limit / display scale")
        self.notes.setText(" · ".join(notes))
        for name in ("can_p", "can_e", "uart_p", "uart_e"):
            unit = "W" if name.endswith("_p") else "J"
            if name == "can_e" and sample["can_e_disabled"]:
                value = f"Disabled (sentinel {sample[name]})"
            elif not sample[name + "_valid"]:
                value = "Unavailable · invalid / not received"
            else:
                value = f"{sample[name]} {unit}"
            freshness = "fresh" if sample[name + "_fresh"] else "stale"
            self.boxes[name].setText(f"{value} · {freshness}")
        decisions = ("Fault disable", "Idle / UVLO", "No source", "Manual", "CAN", "UART", "Measure", "Direct GPIO")
        decision = sample["decision"]
        state = decisions[decision] if 0 <= decision < len(decisions) else f"Unknown ({decision})"
        values = {
            "budget": f"{v.budget_w:g} W · {v.budget_source}" if v.budget_w is not None else v.budget_source,
            "control": f"{state} · SWEN {'ON' if sample['swen_out'] else 'OFF'}",
            "load": f"{v.load_w:.2f} W", "voltage": f"{v.voltage_v:.3f} V",
            "current": f"{v.current_a:+.3f} A",
            "vbus_ovp": "LATCHED" if v.status & 1 else "Clear",
            "vcap_ovp": "LATCHED" if v.status & 2 else "Clear",
            "fresh": "Fresh (≤300 ms)" if v.status & 4 else "Stale / no valid CAN command",
            "status": f"0x{v.status:02X} · reconstructed from T1 flags",
            "ceiling": f"{sample['vcap_max_mV'] / 1000:.3f} V",
        }
        for key, value in values.items():
            self.boxes[key].setText(value)
        self.refresh_quality()

    def refresh_quality(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        stale = self.received_at is not None and now - self.received_at >= self.cfg.stale_after_s
        prefix = "DEMO · simulated T1 · " if self.demo else "T1 telemetry · "
        self.quality.setText(prefix + ("Waiting for valid data" if self.received_at is None else
                                     "STALE · holding last valid values" if stale else "Live"))
        self.bars.stale = stale
        for box in self.boxes.values():
            box.setEnabled(not stale)
        self.bars.update()
