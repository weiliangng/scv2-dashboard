#!/usr/bin/env python3
"""Live SCV2 T1 telemetry dashboard with an optional USB CDC CLI.

The dashboard-facing wire contract is documented in ``docs/PROTOCOL.md``.
This program deliberately accepts only that fixed T1 schema.
"""

from __future__ import annotations

import argparse
from collections import deque
import math
import queue
import socket
import sys
import threading
import time
from dataclasses import dataclass

import numpy as np
import pyqtgraph as pg
import serial
from PySide6 import QtCore, QtGui, QtWidgets
from serial.tools import list_ports


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    unit: str = ""


# Positional order from docs/PROTOCOL.md. Keep this list in lockstep with T1.
FIELDS = (
    Field("seq", "Sequence"), Field("adc_hz", "ADC rate", "Hz"),
    Field("usb_drop", "USB drops"), Field("dma_last", "DMA last", "cycles"),
    Field("dma_max", "DMA max", "cycles"), Field("adc_vcap", "ADC Vcap"),
    Field("adc_vbus", "ADC Vbus"), Field("adc_iload", "ADC Iload"),
    Field("adc_iop", "ADC IMONOP"), Field("adc_ion", "ADC IMONON"),
    Field("vc_mV", "Vcap", "mV"), Field("vb_mV", "Vbus", "mV"),
    Field("il_mA", "Iload", "mA"), Field("iop_mA", "IMONOP", "mA"),
    Field("ion_mA", "IMONON", "mA"), Field("io_mA", "Iout", "mA"),
    Field("ic_mA", "Iconv", "mA"), Field("pset_W", "Power setpoint", "W"),
    Field("btn_in", "Button input"), Field("dir_out", "Direction"),
    Field("swen_out", "Switch enable"), Field("mode_out", "Mode pins"),
    Field("rvsoff_out", "RVSOFF"), Field("nsil_out", "NSIL"), Field("led_out", "Status LED"),
    Field("dac1_ch1", "DAC1 CH1"), Field("dac1_ch2", "DAC1 CH2"),
    Field("dac3_ch1", "DAC3 CH1"), Field("dac3_ch2", "DAC3 CH2"),
    Field("mode_req", "Requested mode"), Field("decision", "Control decision"),
    Field("swen_req", "SWEN request"), Field("safe", "Safety"), Field("uvlo", "UVLO"),
    Field("fault_latched", "Fault latch"), Field("fault_bits", "Fault bits"),
    Field("fault_healthy_ms", "Fault recovery", "ms"), Field("can_bus", "CAN bus"),
    Field("can_p", "CAN power", "W"), Field("can_p_valid", "CAN power valid"),
    Field("can_p_fresh", "CAN power fresh"), Field("can_e", "CAN energy", "J"),
    Field("can_e_valid", "CAN energy valid"), Field("can_e_fresh", "CAN energy fresh"),
    Field("can_e_disabled", "CAN energy disabled"), Field("can_swen", "CAN SWEN"),
    Field("can_swen_valid", "CAN SWEN valid"), Field("can_swen_fresh", "CAN SWEN fresh"),
    Field("uart_p", "UART power", "W"), Field("uart_p_valid", "UART power valid"),
    Field("uart_p_fresh", "UART power fresh"), Field("uart_e", "UART energy", "J"),
    Field("uart_e_valid", "UART energy valid"), Field("uart_e_fresh", "UART energy fresh"),
    Field("uart_swen_req", "UART SWEN request"), Field("man_p", "Manual power", "W"),
    Field("man_p_valid", "Manual power valid"), Field("man_swen", "Manual SWEN"),
    Field("man_swen_valid", "Manual SWEN valid"), Field("btn_swen", "Button SWEN"),
    Field("btn_swen_valid", "Button SWEN valid"), Field("cap_energy_mJ", "Integrated capacitor energy", "mJ"),
    Field("vcap_max_mV", "Runtime Vcap maximum", "mV"),
    Field("cap_unhealthy", "Capacitor health"), Field("cap_bad_windows", "Bad minute streak"),
    Field("cap_derates", "Voltage derates"), Field("cap_dE_mJ_min", "Last energy gain", "mJ/min"),
    Field("cap_dV_mV_min", "Last voltage gain", "mV/min"),
    Field("can_tx_enqueue_fail", "CAN TX enqueue failures"),
)
FIELD_BY_NAME = {field.name: field for field in FIELDS}

VALIDITY_FIELD = {
    "can_p": "can_p_valid", "can_e": "can_e_valid", "uart_p": "uart_p_valid",
    "uart_e": "uart_e_valid", "man_p": "man_p_valid",
}

TEXT = {
    "btn_in": ("LOW", "HIGH"), "dir_out": ("NEGATIVE", "POSITIVE"),
    "swen_out": ("OFF", "ON"), "rvsoff_out": ("OFF", "ON"),
    "nsil_out": ("OFF", "ON"), "led_out": ("OFF", "ON"),
    "swen_req": ("OFF", "ON"), "can_swen": ("OFF", "ON"),
    "uart_swen_req": ("OFF", "ON"), "man_swen": ("OFF", "ON"),
    "btn_swen": ("OFF", "ON"), "safe": ("UNSAFE", "SAFE"),
    "uvlo": ("CLEAR", "LOCKOUT"), "fault_latched": ("CLEAR", "LATCHED"),
    "can_bus": ("DOWN", "UP"), "cap_unhealthy": ("HEALTHY", "UNHEALTHY"),
}
for _name in ("can_p_valid", "can_e_valid", "can_swen_valid", "uart_p_valid", "uart_e_valid",
              "man_p_valid", "man_swen_valid", "btn_swen_valid"):
    TEXT[_name] = ("INVALID", "VALID")
for _name in ("can_p_fresh", "can_e_fresh", "can_swen_fresh", "uart_p_fresh", "uart_e_fresh"):
    TEXT[_name] = ("STALE", "FRESH")

MODE_REQUEST = ("EXTERNAL", "MANUAL", "MEASURE", "DIRECT GPIO")
DECISION = ("FAULT DISABLE", "IDLE / UVLO", "NO SOURCE", "MANUAL", "CAN", "UART", "MEASURE", "DIRECT GPIO")
MODE_OUT = ("BITS 00", "ALGORITHM", "BITS 10", "BITS 11")

GRAPH_SWEEP_SECONDS = 30.0
GRAPH_HISTORY_SECONDS = GRAPH_SWEEP_SECONDS
GRAPH_HISTORY_MAX_SAMPLES = 3_600
GRAPH_REFRESH_HZ = 30.0
CAN_DECISION = DECISION.index("CAN")
UART_DECISION = DECISION.index("UART")


def graph_values(sample: dict[str, int]) -> tuple[float, float, float, float, float]:
    """Return energy, Vcap, Pchassis, Pcap, and requested power in display units."""
    energy = math.nan
    if (
        sample["decision"] == CAN_DECISION
        and sample["can_e_valid"]
        and sample["can_e_fresh"]
        and not sample["can_e_disabled"]
    ):
        energy = float(sample["can_e"])
    elif sample["decision"] == UART_DECISION and sample["uart_e_valid"] and sample["uart_e_fresh"]:
        energy = float(sample["uart_e"])

    vcap = sample["vc_mV"] / 1000.0
    p_chassis = sample["vb_mV"] * sample["il_mA"] / 1_000_000.0
    p_cap = sample["vc_mV"] * sample["io_mA"] / 1_000_000.0
    p_req = float(sample["pset_W"]) - p_chassis
    return energy, vcap, p_chassis, p_cap, p_req


class TelemetryHistory:
    """Time-bounded NumPy ring buffer used by the graph renderer."""

    SERIES_COUNT = 6  # elapsed time plus the five values returned by graph_values()

    def __init__(self, max_seconds: float, max_samples: int) -> None:
        self.max_seconds = max_seconds
        self.max_samples = max_samples
        self._data = np.empty((self.SERIES_COUNT, max_samples), dtype=np.float64)
        self._start = 0
        self._size = 0
        self._time_origin: float | None = None

    def __len__(self) -> int:
        return self._size

    def clear(self) -> None:
        self._start = 0
        self._size = 0
        self._time_origin = None

    def append(self, sample: dict[str, int], timestamp: float | None = None) -> None:
        timestamp = time.monotonic() if timestamp is None else timestamp
        if self._time_origin is None:
            self._time_origin = timestamp
        elapsed = timestamp - self._time_origin

        if self._size < self.max_samples:
            write_index = (self._start + self._size) % self.max_samples
            self._size += 1
        else:
            write_index = self._start
            self._start = (self._start + 1) % self.max_samples
        self._data[:, write_index] = (elapsed, *graph_values(sample))

        cutoff = elapsed - self.max_seconds
        while self._size and self._data[0, self._start] < cutoff:
            self._start = (self._start + 1) % self.max_samples
            self._size -= 1

    def snapshot(self) -> tuple[np.ndarray, ...]:
        if not self._size:
            empty = np.empty(0, dtype=np.float64)
            return tuple(empty for _ in range(self.SERIES_COUNT))
        end = self._start + self._size
        if end <= self.max_samples:
            return tuple(row[self._start:end] for row in self._data)
        split = end % self.max_samples
        return tuple(np.concatenate((row[self._start:], row[:split])) for row in self._data)

def parse_t1(line: str) -> dict[str, int] | None:
    """Parse one complete T1 CSV line; unrelated CLI output is ignored."""
    parts = line.strip().split(",")
    if not parts or parts[0] != "T1":
        return None
    if len(parts) != len(FIELDS) + 1:
        raise ValueError(f"T1 has {len(parts) - 1} values; expected {len(FIELDS)}")
    try:
        return {field.name: int(value, 10) for field, value in zip(FIELDS, parts[1:], strict=True)}
    except ValueError as exc:
        raise ValueError("T1 contains a non-integer value") from exc


def demo_sample(sequence: int) -> dict[str, int]:
    """A valid changing frame for offline UI checks and presentation."""
    t = sequence / 20.0
    sample = {field.name: 0 for field in FIELDS}
    sample.update({
        "seq": sequence, "adc_hz": 100_000, "dma_last": 920 + int(90 * math.sin(t)), "dma_max": 1120,
        "adc_vcap": 2500, "adc_vbus": 2900, "adc_iload": 2050, "adc_iop": 2100, "adc_ion": 2020,
        "vc_mV": 18500 + int(1200 * math.sin(t / 3)), "vb_mV": 24000 + int(400 * math.sin(t / 4)),
        "il_mA": int(2500 * math.sin(t)), "iop_mA": int(2300 * math.sin(t)),
        "ion_mA": int(-1900 * math.sin(t)), "io_mA": int(2200 * math.sin(t)),
        "ic_mA": int(2800 * math.sin(t / 2)), "pset_W": 80, "dir_out": 1, "swen_out": 1,
        "mode_out": 1, "led_out": 1, "dac1_ch1": 2200, "dac1_ch2": 1800, "dac3_ch1": 2048,
        "dac3_ch2": 2048, "mode_req": 0, "decision": 4, "swen_req": 1, "safe": 1,
        "fault_bits": 0, "can_bus": 1, "can_p": 80, "can_p_valid": 1, "can_p_fresh": 1,
        "can_e": 40, "can_e_valid": 1, "can_e_fresh": 1, "can_swen": 1, "can_swen_valid": 1,
        "can_swen_fresh": 1, "cap_energy_mJ": int(5_000_000 * math.sin(t / 10)),
        "vcap_max_mV": 26200, "cap_unhealthy": 0, "cap_bad_windows": 2,
        "cap_derates": 1, "cap_dE_mJ_min": 550000, "cap_dV_mV_min": 50,
        "can_tx_enqueue_fail": 2,
    })
    return sample


class NewlineStreamParser:
    """Reassemble newline-delimited records from arbitrary byte chunks."""

    MAX_BUFFER_BYTES = 1_048_576

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[bytes]:
        """Return complete records, excluding their LF delimiter, in arrival order."""
        self._buffer.extend(chunk)
        if len(self._buffer) > self.MAX_BUFFER_BYTES:
            self._buffer.clear()
            raise ValueError("unterminated stream data exceeded 1 MiB")

        records: list[bytes] = []
        while (newline := self._buffer.find(b"\n")) >= 0:
            records.append(bytes(self._buffer[:newline]))
            del self._buffer[:newline + 1]
        return records


class SerialReader(threading.Thread):
    CLI_PROMPT = b"scv2> "
    CLI_TIMEOUT_SECONDS = 5.0

    def __init__(self, port: str, baud: int, enable_telemetry: bool, events: queue.Queue) -> None:
        super().__init__(name="scv2-serial-reader", daemon=True)
        self.port, self.baud, self.enable_telemetry, self.events = port, baud, enable_telemetry, events
        self.stop_requested = threading.Event()
        self.commands: queue.Queue[str] = queue.Queue()
        self._serial: serial.Serial | None = None
        self._started_telemetry = False

    def stop(self) -> None:
        self.stop_requested.set()

    def submit_command(self, command: str) -> None:
        """Schedule one USB CDC command; all serial I/O remains on this thread."""
        self.commands.put(command)

    def _write_cli(self, command: str) -> None:
        assert self._serial is not None
        self._serial.write(command.encode("utf-8") + b"\r\n")
        self._serial.flush()

    def _read_cli_response(self) -> str:
        """Read through the CLI prompt, which is the firmware's command-complete marker."""
        assert self._serial is not None
        deadline = time.monotonic() + self.CLI_TIMEOUT_SECONDS
        response = bytearray()
        while time.monotonic() < deadline:
            if self.stop_requested.is_set():
                raise InterruptedError("Disconnected while waiting for the CLI response.")
            chunk = self._serial.read(256)
            if chunk:
                response.extend(chunk)
                if self.CLI_PROMPT in response:
                    return response.decode("utf-8", errors="replace")
        raise TimeoutError(f"No CLI prompt received within {self.CLI_TIMEOUT_SECONDS:g} seconds.")

    def _run_command(self, command: str) -> None:
        """Pause the USB telemetry mirror, execute one command, then restore it."""
        response = ""
        error: str | None = None
        try:
            if self._started_telemetry:
                self._write_cli("telemetry off")
                self._read_cli_response()
            self._write_cli(command)
            response = self._read_cli_response()
        except (OSError, serial.SerialException, TimeoutError, InterruptedError) as exc:
            error = str(exc)
        finally:
            if self._started_telemetry and not self.stop_requested.is_set():
                try:
                    self._write_cli("telemetry on")
                except (OSError, serial.SerialException) as exc:
                    error = error or f"Could not restore USB telemetry: {exc}"
        self.events.put(("command_result", command, response, error))

    def run(self) -> None:
        try:
            self._serial = serial.Serial(self.port, self.baud, timeout=0.2, write_timeout=0.5)
            if self.enable_telemetry:
                self._write_cli("telemetry on")
                self._started_telemetry = True
            self.events.put(("connected", self.port))
            while not self.stop_requested.is_set():
                try:
                    command = self.commands.get_nowait()
                except queue.Empty:
                    command = None
                if command is not None:
                    self._run_command(command)
                    continue
                # readline() returns as soon as a telemetry newline arrives.  In
                # contrast, read(4096) waits for a large batch or the serial
                # timeout, which adds visible latency to the dashboard.
                raw = self._serial.readline()
                if not raw:
                    continue
                self.events.put(("packet", raw))
        except (OSError, serial.SerialException) as exc:
            self.events.put(("error", str(exc)))
        finally:
            if self._serial is not None:
                try:
                    if self._started_telemetry:
                        self._write_cli("telemetry off")
                    self._serial.close()
                except (OSError, serial.SerialException):
                    pass
            self.events.put(("disconnected", self.port))


class UdpReader(threading.Thread):
    """Receive raw UART bridge datagrams by binding a local UDP port only."""

    def __init__(self, port: int, events: queue.Queue) -> None:
        super().__init__(name="scv2-udp-reader", daemon=True)
        self.port, self.events = port, events
        self.stop_requested = threading.Event()
        self._socket: socket.socket | None = None

    def stop(self) -> None:
        self.stop_requested.set()

    def run(self) -> None:
        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._socket.bind(("", self.port))
            self._socket.settimeout(0.2)
            self.events.put(("connected", f"UDP :{self.port}"))
            while not self.stop_requested.is_set():
                try:
                    raw, sender = self._socket.recvfrom(65_535)
                except TimeoutError:
                    continue
                self.events.put(("packet", raw, sender))
        except OSError as exc:
            self.events.put(("error", str(exc)))
        finally:
            if self._socket is not None:
                self._socket.close()
            self.events.put(("disconnected", f"UDP :{self.port}"))


class StatusCard(QtWidgets.QFrame):
    def __init__(self, title: str) -> None:
        super().__init__()
        self.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.title = QtWidgets.QLabel(title)
        self.value = QtWidgets.QLabel("—")
        self.value.setWordWrap(True)
        self.title.setStyleSheet("font-size: 10px; color: #cbd5e1;")
        self.value.setStyleSheet("font-weight: 700; font-size: 14px;")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)
        layout.addWidget(self.title)
        layout.addWidget(self.value)
        self.set_state("—", "grey")

    def set_state(self, text: str, state: str) -> None:
        backgrounds = {"grey": "#444b55", "green": "#176b48", "red": "#8d2631", "amber": "#8a5a10", "blue": "#1c5683"}
        self.value.setText(text)
        self.setStyleSheet(f"StatusCard {{ background: {backgrounds[state]}; border: 1px solid #718096; border-radius: 5px; }}")


class Dashboard(QtWidgets.QMainWindow):
    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__()
        self.args = args
        self.events: queue.Queue = queue.Queue(maxsize=2000)
        self.reader: SerialReader | UdpReader | None = None
        self.stream_parser = NewlineStreamParser()
        self.raw_packets: deque[bytes] = deque(maxlen=200)
        self.raw_messages: deque[bytes] = deque(maxlen=500)
        self.data_sample_times: deque[float] = deque()
        self.packet_count = 0
        self.last_packet_time: float | None = None
        self.listen_started_time: float | None = None
        self.last_sender: tuple[str, int] | None = None
        self.connection_error: str | None = None
        self.serial_connected = False
        self.demo_sequence = 0
        self.last_sample: dict[str, int] | None = None
        self.last_seq: int | None = None
        self.frame_gaps = 0
        self.status_cards: dict[str, StatusCard] = {}
        self.numeric_cards: dict[str, StatusCard] = {}
        self.graph_history = TelemetryHistory(GRAPH_HISTORY_SECONDS, GRAPH_HISTORY_MAX_SAMPLES)
        self.graph_curves: tuple[pg.PlotDataItem, ...] = ()
        self.graph_plots: tuple[pg.PlotItem, ...] = ()
        self.graph_sweep_started_at: float | None = None
        self.graphs_dirty = False

        self.setWindowTitle("SCV2 Telemetry Dashboard")
        self.resize(1500, 950)
        self._build_ui()
        self.refresh_ports()

        self.poll_timer = QtCore.QTimer(self)
        self.poll_timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
        self.poll_timer.timeout.connect(self.poll_events)
        self.ui_refresh_hz = self.display_refresh_hz()
        self.poll_timer.start(max(1, round(1000 / self.ui_refresh_hz)))
        self.graph_timer = QtCore.QTimer(self)
        self.graph_timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
        self.graph_timer.timeout.connect(self.refresh_graphs)
        self.graph_timer.start(round(1000 / GRAPH_REFRESH_HZ))
        self.demo_timer = QtCore.QTimer(self)
        self.demo_timer.timeout.connect(self.add_demo_sample)
        if args.demo:
            self.demo_timer.start(20)
            self.connection.setText("DEMO — simulated T1 telemetry")
            self.packet_status.setText("Simulated packets")
            self.packet_status.setStyleSheet("font-weight: 700; color: #15803d;")
            self.data_rate.setText("Data: demo")
        if args.exit_after:
            QtCore.QTimer.singleShot(int(args.exit_after * 1000), self.close)

    @staticmethod
    def display_refresh_hz() -> float:
        """Use the display cadence as the maximum useful card-refresh rate."""
        screen = QtGui.QGuiApplication.primaryScreen()
        refresh_hz = screen.refreshRate() if screen is not None else 60.0
        return refresh_hz if refresh_hz > 1.0 else 60.0

    def _build_ui(self) -> None:
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        root_layout = QtWidgets.QVBoxLayout(root)
        root_layout.setContentsMargins(8, 8, 8, 8)

        controls = QtWidgets.QHBoxLayout()
        self.transport_combo = QtWidgets.QComboBox()
        self.transport_combo.addItem("USB serial", "serial")
        self.transport_combo.addItem("UDP listener", "udp")
        self.transport_combo.setCurrentIndex(1 if self.args.transport == "udp" else 0)
        self.transport_combo.currentIndexChanged.connect(self.update_transport_controls)
        self.port_combo = QtWidgets.QComboBox()
        self.port_combo.setMinimumWidth(180)
        self.baud = QtWidgets.QSpinBox()
        self.baud.setRange(1200, 2_000_000)
        self.baud.setValue(self.args.baud)
        self.udp_port = QtWidgets.QSpinBox()
        self.udp_port.setRange(1, 65_535)
        self.udp_port.setValue(self.args.udp_port)
        self.refresh_button = QtWidgets.QPushButton("Refresh ports")
        self.refresh_button.clicked.connect(self.refresh_ports)
        self.connect_button = QtWidgets.QPushButton("Connect")
        self.connect_button.clicked.connect(self.toggle_connection)
        self.auto_telemetry = QtWidgets.QCheckBox("Enable USB telemetry while connected")
        self.auto_telemetry.setChecked(True)
        self.connection = QtWidgets.QLabel("Disconnected")
        self.connection.setStyleSheet("font-weight: 700;")
        self.packet_status = QtWidgets.QLabel("Not receiving")
        self.packet_status.setStyleSheet("font-weight: 700; color: #64748b;")
        self.data_rate = QtWidgets.QLabel("Data: 0 Hz")
        self.data_rate.setToolTip("Valid T1 records received during the preceding second.")
        self.data_rate.setStyleSheet("font-weight: 700; color: #1d4ed8;")
        controls.addWidget(QtWidgets.QLabel("Source"))
        controls.addWidget(self.transport_combo)
        controls.addWidget(QtWidgets.QLabel("Port"))
        controls.addWidget(self.port_combo)
        controls.addWidget(QtWidgets.QLabel("Baud"))
        controls.addWidget(self.baud)
        controls.addWidget(QtWidgets.QLabel("UDP port"))
        controls.addWidget(self.udp_port)
        controls.addWidget(self.refresh_button)
        controls.addWidget(self.connect_button)
        controls.addWidget(self.auto_telemetry)
        controls.addStretch(1)
        controls.addWidget(self.connection)
        controls.addWidget(self.packet_status)
        controls.addWidget(self.data_rate)
        root_layout.addLayout(controls)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(self._live_values_page(), "Values")
        self.graphs_page = self._graphs_page()
        self.tabs.addTab(self.graphs_page, "Graphs")
        self.tabs.addTab(self._status_page(), "Status")
        self.tabs.addTab(self._command_page(), "USB CLI")
        self.tabs.currentChanged.connect(self._tab_changed)
        root_layout.addWidget(self.tabs)
        self.update_transport_controls()

    def using_udp(self) -> bool:
        return self.transport_combo.currentData() == "udp"

    def update_transport_controls(self) -> None:
        udp = self.using_udp()
        connected = self.reader is not None
        self.port_combo.setEnabled(not udp and not connected)
        self.baud.setEnabled(not udp and not connected)
        self.refresh_button.setEnabled(not udp and not connected)
        self.auto_telemetry.setEnabled(not udp and not connected)
        self.udp_port.setEnabled(udp and not connected)
        self.transport_combo.setEnabled(not connected)
        command_available = self.serial_connected and self.auto_telemetry.isChecked()
        self.command_input.setEnabled(command_available)
        self.command_button.setEnabled(command_available)
        if command_available:
            self.command_hint.setText("Telemetry is paused only while the command runs, then restored automatically.")
        elif udp:
            self.command_hint.setText("USB CLI commands are unavailable for the UDP listener.")
        elif connected:
            self.command_hint.setText("Commands require Enable USB telemetry while connected, selected before connecting.")
        else:
            self.command_hint.setText("Connect through USB serial with USB telemetry enabled to send a CLI command.")

    def _card_grid(self, title: str, names: list[str], columns: int = 4) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox(title)
        grid = QtWidgets.QGridLayout(box)
        grid.setSpacing(6)
        for index, name in enumerate(names):
            card = StatusCard(FIELD_BY_NAME[name].label)
            self.status_cards[name] = card
            grid.addWidget(card, index // columns, index % columns)
        return box

    def _value_grid(self, title: str, names: list[str], columns: int = 4) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox(title)
        grid = QtWidgets.QGridLayout(box)
        grid.setSpacing(6)
        for index, name in enumerate(names):
            card = StatusCard(FIELD_BY_NAME[name].label)
            self.numeric_cards[name] = card
            grid.addWidget(card, index // columns, index % columns)
        return box

    def _live_values_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QGridLayout(page)
        layout.addWidget(self._value_grid("Calibrated electrical values", ["vb_mV", "vc_mV", "il_mA", "iop_mA", "ion_mA", "io_mA", "ic_mA", "pset_W", "cap_energy_mJ"]), 0, 0)
        layout.addWidget(self._value_grid("Raw ADC values", ["adc_vcap", "adc_vbus", "adc_iload", "adc_iop", "adc_ion"]), 0, 1)
        layout.addWidget(self._value_grid("DAC values", ["dac1_ch1", "dac1_ch2", "dac3_ch1", "dac3_ch2"]), 1, 0)
        layout.addWidget(self._value_grid("Command values", ["can_p", "can_e", "uart_p", "uart_e", "man_p"]), 1, 1)
        layout.addWidget(self._value_grid("Capacitor health", ["vcap_max_mV", "cap_bad_windows", "cap_derates", "cap_dE_mJ_min", "cap_dV_mV_min"]), 2, 0)
        layout.addWidget(self._value_grid("Telemetry and diagnostics", ["seq", "adc_hz", "usb_drop", "dma_last", "dma_max", "fault_healthy_ms", "can_tx_enqueue_fail"]), 2, 1)
        return page

    def _graphs_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)

        controls = QtWidgets.QHBoxLayout()
        sweep_label = QtWidgets.QLabel("30 s oscilloscope sweep — trace clears at the end of each pass")
        sweep_label.setStyleSheet("font-weight: 700;")
        clear_button = QtWidgets.QPushButton("Clear history")
        clear_button.clicked.connect(self.clear_graph_history)
        self.graph_status = QtWidgets.QLabel("No graph samples")
        self.graph_status.setStyleSheet("color: #64748b;")
        controls.addWidget(sweep_label)
        controls.addWidget(clear_button)
        controls.addStretch(1)
        controls.addWidget(self.graph_status)
        layout.addLayout(controls)

        pg.setConfigOptions(antialias=False)
        canvas = pg.GraphicsLayoutWidget()
        layout.addWidget(canvas, 1)
        specs = (
            ("Energy Buffer (Virtual)", "J", 0.0, 70.0),
            ("V_Cap", "V", 0.0, 30.0),
            ("P_Chassis", "W", -50.0, 400.0),
            ("P_Cap and P_Req", "W", -260.0, 260.0),
        )
        plots: list[pg.PlotItem] = []
        positions = ((0, 0), (0, 1), (1, 0), (1, 1))
        for (title, unit, y_min, y_max), (row, column) in zip(specs, positions, strict=True):
            plot = canvas.addPlot(row=row, col=column, title=title)
            plot.showGrid(x=True, y=True, alpha=0.2)
            plot.setLabel("left", unit)
            plot.setDownsampling(auto=True, mode="peak")
            plot.setClipToView(True)
            plot.disableAutoRange(axis=pg.ViewBox.YAxis)
            plot.setYRange(y_min, y_max, padding=0.0)
            plot.setMouseEnabled(x=True, y=False)
            plot.setXRange(0.0, GRAPH_SWEEP_SECONDS, padding=0.0)
            plot.setLimits(xMin=0.0, xMax=GRAPH_SWEEP_SECONDS, minXRange=0.1, maxXRange=GRAPH_SWEEP_SECONDS)
            if plots:
                plot.setXLink(plots[0])
            plots.append(plot)
        plots[2].setLabel("bottom", "Sweep time", units="s")
        plots[3].setLabel("bottom", "Sweep time", units="s")
        plots[-1].addLegend(offset=(10, 10))

        self.graph_plots = tuple(plots)
        self.graph_curves = (
            plots[0].plot(pen=pg.mkPen("#a855f7", width=1), connect="finite"),
            plots[1].plot(pen=pg.mkPen("#2563eb", width=1), connect="finite"),
            plots[2].plot(pen=pg.mkPen("#dc2626", width=1), connect="finite"),
            plots[3].plot(name="P_Cap", pen=pg.mkPen("#16a34a", width=1), connect="finite"),
            plots[3].plot(name="P_Req", pen=pg.mkPen("#f59e0b", width=1), connect="finite"),
        )
        return page

    def _tab_changed(self, _index: int) -> None:
        if self.tabs.currentWidget() is self.graphs_page:
            self.refresh_graphs(force=True)

    def clear_graph_history(self) -> None:
        self.graph_history.clear()
        self.graph_sweep_started_at = None
        self.graphs_dirty = True
        self.refresh_graphs(force=True)

    def record_graph_sample(self, sample: dict[str, int], timestamp: float | None = None) -> None:
        timestamp = time.monotonic() if timestamp is None else timestamp
        if self.graph_sweep_started_at is None:
            self.graph_sweep_started_at = timestamp
        elif timestamp - self.graph_sweep_started_at >= GRAPH_SWEEP_SECONDS:
            self.graph_history.clear()
            self.graph_sweep_started_at = timestamp
        self.graph_history.append(sample, timestamp)
        self.graphs_dirty = True

    def refresh_graphs(self, force: bool = False) -> None:
        if self.tabs.currentWidget() is not self.graphs_page:
            return
        if not force and not self.graphs_dirty:
            return

        elapsed, *series = self.graph_history.snapshot()
        for curve, values in zip(self.graph_curves, series, strict=True):
            curve.setData(elapsed, values)
        self.graphs_dirty = False

        if not elapsed.size:
            self.graph_status.setText("No graph samples | 30 s sweep | 30 Hz redraw cap")
            return
        self.graph_status.setText(
            f"{elapsed.size:,} samples | sweep {elapsed[-1]:.1f} / {GRAPH_SWEEP_SECONDS:.0f} s | 30 Hz redraw cap"
        )

    def _status_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        states = QtWidgets.QGridLayout(page)
        states.addWidget(self._card_grid("Physical I/O", ["btn_in", "dir_out", "swen_out", "mode_out", "rvsoff_out", "nsil_out", "led_out"]), 0, 0)
        states.addWidget(self._card_grid("Control and safety", ["mode_req", "decision", "swen_req", "safe", "uvlo", "fault_latched", "fault_bits", "cap_unhealthy"]), 0, 1)
        states.addWidget(self._card_grid("CAN", ["can_bus", "can_p_valid", "can_p_fresh", "can_e_valid", "can_e_fresh", "can_e_disabled", "can_swen", "can_swen_valid", "can_swen_fresh"]), 1, 0)
        states.addWidget(self._card_grid("UART and manual", ["uart_p_valid", "uart_p_fresh", "uart_e_valid", "uart_e_fresh", "uart_swen_req", "man_p_valid", "man_swen", "man_swen_valid", "btn_swen", "btn_swen_valid"]), 1, 1)
        return page

    def _command_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        warning = QtWidgets.QLabel(
            "Commands are sent only over the SCV2 USB CDC connection. They can change hardware state; use a safe bench setup."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("font-weight: 700; color: #b45309;")
        self.command_hint = QtWidgets.QLabel()
        self.command_hint.setWordWrap(True)
        command_row = QtWidgets.QHBoxLayout()
        self.command_input = QtWidgets.QLineEdit()
        self.command_input.setPlaceholderText("For example: status")
        self.command_input.returnPressed.connect(self.send_command)
        self.command_button = QtWidgets.QPushButton("Send command")
        self.command_button.clicked.connect(self.send_command)
        command_row.addWidget(QtWidgets.QLabel("SCV2 CLI command"))
        command_row.addWidget(self.command_input, 1)
        command_row.addWidget(self.command_button)
        self.command_result = QtWidgets.QPlainTextEdit()
        self.command_result.setReadOnly(True)
        self.command_result.setPlaceholderText("Command responses appear here.")
        layout.addWidget(warning)
        layout.addWidget(self.command_hint)
        layout.addLayout(command_row)
        layout.addWidget(self.command_result, 1)
        return page

    def send_command(self) -> None:
        command = self.command_input.text().strip()
        if not command:
            return
        if "\r" in command or "\n" in command:
            QtWidgets.QMessageBox.warning(self, "One command at a time", "Enter one SCV2 CLI command without a line break.")
            return
        if not isinstance(self.reader, SerialReader) or not self.serial_connected or not self.auto_telemetry.isChecked():
            return
        self.command_input.clear()
        self.command_input.setEnabled(False)
        self.command_button.setEnabled(False)
        self.command_hint.setText(f"Running: {command}")
        self.reader.submit_command(command)

    def refresh_ports(self) -> None:
        selected = self.port_combo.currentText()
        self.port_combo.clear()
        ports = list(list_ports.comports())
        self.port_combo.addItems([f"{port.device} — {port.description}" for port in ports])
        if self.args.port and not ports:
            self.port_combo.addItem(self.args.port)
        for index in range(self.port_combo.count()):
            if self.args.port and self.port_combo.itemText(index).startswith(self.args.port):
                self.port_combo.setCurrentIndex(index)
            elif selected and self.port_combo.itemText(index) == selected:
                self.port_combo.setCurrentIndex(index)

    def selected_port(self) -> str:
        return self.port_combo.currentText().split(" — ", 1)[0].strip()

    def toggle_connection(self) -> None:
        if self.reader is not None:
            self.reader.stop()
            self.connect_button.setEnabled(False)
            self.command_input.setEnabled(False)
            self.command_button.setEnabled(False)
            return
        self.stream_parser = NewlineStreamParser()
        self.packet_count = 0
        self.data_sample_times.clear()
        self.last_packet_time = None
        self.listen_started_time = time.monotonic()
        self.last_sender = None
        self.connection_error = None
        self.clear_graph_history()
        if self.using_udp():
            self.reader = UdpReader(self.udp_port.value(), self.events)
            self.connection.setText(f"Binding UDP :{self.udp_port.value()}…")
        else:
            port = self.selected_port()
            if not port:
                QtWidgets.QMessageBox.warning(self, "No serial port", "Connect the SCV2 USB device, then click Refresh ports.")
                return
            self.reader = SerialReader(port, self.baud.value(), self.auto_telemetry.isChecked(), self.events)
            self.connection.setText(f"Connecting to {port}…")
        self.reader.start()
        self.connect_button.setText("Disconnect")
        self.packet_status.setText("Waiting for packets…")
        self.packet_status.setStyleSheet("font-weight: 700; color: #b45309;")
        self.update_transport_controls()

    def add_demo_sample(self) -> None:
        self.demo_sequence += 1
        self.consume_sample(demo_sample(self.demo_sequence))

    def poll_events(self) -> None:
        latest_sample: dict[str, int] | None = None
        while True:
            try:
                message = self.events.get_nowait()
            except queue.Empty:
                break
            event, data, *extra = message
            if event == "sample":
                if self.last_seq is not None and data["seq"] > self.last_seq + 1:
                    self.frame_gaps += data["seq"] - self.last_seq - 1
                self.last_seq = data["seq"]
                self.record_graph_sample(data)
                latest_sample = data
            elif event == "packet":
                sample = self.consume_packet(data, extra[0] if extra else None)
                if sample is not None:
                    latest_sample = sample
            elif event == "connected":
                self.connection.setText(f"Connected: {data}")
                self.serial_connected = isinstance(self.reader, SerialReader)
                self.update_transport_controls()
            elif event == "error":
                self.connection_error = data
                self.connection.setText(f"Error: {data}")
                self.packet_status.setText("Receiver error")
                self.packet_status.setStyleSheet("font-weight: 700; color: #b91c1c;")
            elif event == "parse_error":
                self.connection.setText(f"Ignored malformed telemetry: {data}")
            elif event == "command_result":
                command, response, error = data, extra[0], extra[1]
                if error is None:
                    clean_response = response.replace("scv2> ", "").strip()
                    self.command_result.appendPlainText(f"> {command}\n{clean_response or '(no response)'}\n")
                    self.command_hint.setText("Command complete; USB telemetry restored.")
                else:
                    self.command_result.appendPlainText(f"> {command}\nERROR: {error}\n")
                    self.command_hint.setText("Command failed; attempted to restore USB telemetry.")
                self.update_transport_controls()
            elif event == "disconnected":
                self.reader = None
                self.serial_connected = False
                self.connect_button.setEnabled(True)
                self.connect_button.setText("Connect")
                if not self.args.demo and self.connection_error is None:
                    self.connection.setText("Disconnected")
                    self.packet_status.setText("Not receiving")
                    self.packet_status.setStyleSheet("font-weight: 700; color: #64748b;")
                self.update_transport_controls()
        if latest_sample is not None:
            self.consume_sample(latest_sample, record_history=False)
        self.update_packet_status()
        self.update_data_rate()

    def consume_packet(self, raw: bytes, sender: tuple[str, int] | None) -> dict[str, int] | None:
        """Keep incoming datagrams intact, then parse complete stream records from them."""
        self.raw_packets.append(raw)
        self.packet_count += 1
        self.last_packet_time = time.monotonic()
        if sender is not None:
            self.last_sender = sender
        try:
            records = self.stream_parser.feed(raw)
        except ValueError as exc:
            self.connection.setText(f"Stream reset: {exc}")
            return None
        latest_sample: dict[str, int] | None = None
        for raw_record in records:
            self.raw_messages.append(raw_record)
            try:
                # UART data is raw bytes; replacement keeps malformed UTF-8 display-safe.
                sample = parse_t1(raw_record.decode("utf-8", errors="replace"))
                if sample is not None:
                    if self.last_seq is not None and sample["seq"] > self.last_seq + 1:
                        self.frame_gaps += sample["seq"] - self.last_seq - 1
                    self.last_seq = sample["seq"]
                    sample_time = time.monotonic()
                    self.data_sample_times.append(sample_time)
                    self.record_graph_sample(sample, sample_time)
                    latest_sample = sample
            except ValueError as exc:
                self.connection.setText(f"Ignored malformed telemetry: {exc}")
        return latest_sample

    def update_packet_status(self) -> None:
        if self.args.demo or self.reader is None:
            return
        if self.last_packet_time is None:
            waiting = time.monotonic() - (self.listen_started_time or time.monotonic())
            if waiting >= self.args.packet_timeout:
                text, color = f"No packets received for {waiting:.1f} s", "#b91c1c"
            else:
                text, color = "Waiting for packets…", "#b45309"
        else:
            age = time.monotonic() - self.last_packet_time
            if age >= self.args.packet_timeout:
                text, color = f"No packets for {age:.1f} s", "#b91c1c"
            else:
                source = f" from {self.last_sender[0]}:{self.last_sender[1]}" if self.last_sender else ""
                text, color = f"Receiving: {self.packet_count} packets{source}", "#15803d"
        self.packet_status.setText(text)
        self.packet_status.setStyleSheet(f"font-weight: 700; color: {color};")

    def update_data_rate(self) -> None:
        if self.args.demo:
            return
        cutoff = time.monotonic() - 1.0
        while self.data_sample_times and self.data_sample_times[0] < cutoff:
            self.data_sample_times.popleft()
        self.data_rate.setText(f"Data: {len(self.data_sample_times)} Hz")

    def consume_sample(self, sample: dict[str, int], record_history: bool = True) -> None:
        if record_history:
            self.record_graph_sample(sample)
        self.last_sample = sample
        for name, card in self.numeric_cards.items():
            if name == "cap_energy_mJ":
                card.set_state(f"{sample[name] / 1000.0:,.3f} J", "blue")
                continue
            if name == "vcap_max_mV":
                card.set_state(f"{sample[name] / 1000.0:,.3f} V", "blue")
                continue
            if name == "cap_dE_mJ_min":
                card.set_state(f"{sample[name] / 1000.0:,.3f} J/min", "blue")
                continue
            if name == "can_tx_enqueue_fail":
                text, state = status_text_and_state(name, sample)
                card.set_state(text, state)
                continue
            if name in VALIDITY_FIELD:
                text, state = status_text_and_state(name, sample)
                card.set_state(text, state)
                continue
            field = FIELD_BY_NAME[name]
            suffix = f" {field.unit}" if field.unit else ""
            card.set_state(f"{sample[name]:,}{suffix}", "blue")
        for name, card in self.status_cards.items():
            text, state = status_text_and_state(name, sample)
            card.set_state(text, state)
        self.connection.setText(f"Live — gaps: {self.frame_gaps}  |  T1 sequence: {sample['seq']}")

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802
        if self.reader is not None:
            self.reader.stop()
            self.reader.join(timeout=1.0)
        event.accept()


def status_text_and_state(name: str, sample: dict[str, int]) -> tuple[str, str]:
    value = sample[name]
    if name in VALIDITY_FIELD:
        valid = sample[VALIDITY_FIELD[name]]
        field = FIELD_BY_NAME[name]
        if name == "can_e" and sample["can_e_disabled"]:
            return "DISABLED", "grey"
        return (f"{value} {field.unit}" if valid else "INVALID"), ("blue" if valid else "grey")
    if name == "mode_req":
        return enum_text(MODE_REQUEST, value), "amber" if value == 3 else "blue"
    if name == "decision":
        if 0 <= value < len(DECISION):
            color = "red" if value == 0 else "grey" if value in (1, 2) else "amber" if value == 7 else "green"
            return DECISION[value], color
        return f"UNKNOWN ({value})", "red"
    if name == "mode_out":
        return enum_text(MODE_OUT, value), "green" if value == 1 else "amber"
    if name == "fault_bits":
        labels = {0: "NO FAULT", 1: "VBUS OVP", 2: "VCAP OVP", 3: "VBUS + VCAP OVP"}
        return labels.get(value, f"UNKNOWN (0x{value:X})"), "green" if value == 0 else "red"
    if name == "can_tx_enqueue_fail":
        return f"{value:,}", "green" if value == 0 else "red"
    if name in TEXT:
        off, on = TEXT[name]
        text = on if value else off
        if name in {"safe"}:
            return text, "green" if value else "red"
        if name in {"uvlo", "fault_latched", "cap_unhealthy"}:
            return text, "red" if value else "green"
        if name.endswith("_valid") or name.endswith("_fresh"):
            return text, "green" if value else "grey"
        return text, "green" if value else "grey"
    return str(value), "blue"


def enum_text(values: tuple[str, ...], value: int) -> str:
    return values[value] if 0 <= value < len(values) else f"UNKNOWN ({value})"


def self_test() -> None:
    class FakeSerial:
        def __init__(self, chunks: list[bytes]) -> None:
            self.chunks = deque(chunks)
            self.writes: list[bytes] = []

        def write(self, data: bytes) -> int:
            self.writes.append(data)
            return len(data)

        def flush(self) -> None:
            pass

        def read(self, _size: int) -> bytes:
            return self.chunks.popleft() if self.chunks else b""

    values = demo_sample(42)
    line = "T1," + ",".join(str(values[field.name]) for field in FIELDS)
    parsed = parse_t1(line)
    assert parsed == values
    negative_energy = {**values, "cap_energy_mJ": -1234}
    negative_line = "T1," + ",".join(str(negative_energy[field.name]) for field in FIELDS)
    assert parse_t1(negative_line)["cap_energy_mJ"] == -1234
    assert parse_t1("CLI ready") is None
    assert status_text_and_state("decision", parsed) == ("CAN", "green")
    assert status_text_and_state("can_p", {**parsed, "can_p_valid": 0}) == ("INVALID", "grey")
    assert status_text_and_state("cap_unhealthy", {**parsed, "cap_unhealthy": 1}) == ("UNHEALTHY", "red")
    assert status_text_and_state("can_tx_enqueue_fail", {**parsed, "can_tx_enqueue_fail": 0}) == ("0", "green")
    assert status_text_and_state("can_tx_enqueue_fail", parsed) == ("2", "red")
    energy, vcap, p_chassis, p_cap, p_req = graph_values(parsed)
    assert energy == 40.0
    assert math.isclose(vcap, parsed["vc_mV"] / 1000.0)
    assert math.isclose(p_chassis, parsed["vb_mV"] * parsed["il_mA"] / 1_000_000.0)
    assert math.isclose(p_cap, parsed["vc_mV"] * parsed["io_mA"] / 1_000_000.0)
    assert math.isclose(p_req, parsed["pset_W"] - p_chassis)
    uart_energy = graph_values({
        **parsed, "decision": UART_DECISION, "uart_e": 23, "uart_e_valid": 1, "uart_e_fresh": 1,
    })[0]
    assert uart_energy == 23.0
    assert math.isnan(graph_values({**parsed, "decision": CAN_DECISION, "can_e_fresh": 0})[0])
    assert math.isnan(graph_values({**parsed, "decision": DECISION.index("MANUAL")})[0])
    history = TelemetryHistory(max_seconds=2.0, max_samples=3)
    for offset in range(4):
        history.append({**parsed, "seq": offset}, timestamp=10.0 + offset)
    history_elapsed, *_history_values = history.snapshot()
    assert len(history) == 3
    assert np.array_equal(history_elapsed, np.array([1.0, 2.0, 3.0]))
    history.append(parsed, timestamp=14.5)
    history_elapsed, *_history_values = history.snapshot()
    assert np.array_equal(history_elapsed, np.array([3.0, 4.5]))
    history.clear()
    assert len(history) == 0
    stream = NewlineStreamParser()
    assert stream.feed(b"CLI ready\nT1,1") == [b"CLI ready"]
    assert stream.feed(b",2\r\nlast") == [b"T1,1,2\r"]
    assert stream.feed(b" message\n") == [b"last message"]
    command_events: queue.Queue = queue.Queue()
    command_reader = SerialReader("COM1", 115200, True, command_events)
    fake_serial = FakeSerial([b"ok\r\nscv2> ", b"status output\r\nscv2> "])
    command_reader._serial = fake_serial  # Test the serial-thread transaction without hardware.
    command_reader._started_telemetry = True
    command_reader._run_command("status")
    assert fake_serial.writes == [b"telemetry off\r\n", b"status\r\n", b"telemetry on\r\n"]
    assert command_events.get_nowait() == ("command_result", "status", "status output\r\nscv2> ", None)
    print("T1 parser, graph calculations/history, UDP buffering, USB CLI, and display self-test passed.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="COM port to open, for example COM8")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--transport", choices=("serial", "udp"), default="serial", help="Initial input source")
    parser.add_argument("--udp-port", type=int, default=14551, help="Local UDP port to bind when using the UDP listener")
    parser.add_argument("--packet-timeout", type=float, default=3.0, help="Seconds without a packet before showing a timeout")
    parser.add_argument("--demo", action="store_true", help="Show simulated telemetry without hardware")
    parser.add_argument("--exit-after", type=float, help="Close automatically after this many seconds (test helper)")
    parser.add_argument("--self-test", action="store_true", help="Validate CSV parsing and display rules, then exit")
    args = parser.parse_args()
    if not 1 <= args.udp_port <= 65_535:
        parser.error("--udp-port must be between 1 and 65535")
    if args.packet_timeout <= 0:
        parser.error("--packet-timeout must be positive")
    if args.self_test:
        self_test()
        return 0
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    window = Dashboard(args)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
