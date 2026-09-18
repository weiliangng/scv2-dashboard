"""Timestamped TCP batching, stream recovery, and ESP-only graph timing."""

import argparse
import os
from pathlib import Path
import queue
import socket
import sys
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6 import QtWidgets
from scv2_dashboard import Dashboard, FIELDS, TcpReader, demo_sample
from wireless import WirelessRecord, WirelessTimeline


def wire_record(seq, captured_us, *, boot=17, prepared_us=None):
    sample = demo_sample(seq)
    line = "T1," + ",".join(str(sample[field.name]) for field in FIELDS)
    prepared_us = captured_us + 100_000 if prepared_us is None else prepared_us
    return f"W1,{boot},{captured_us},{prepared_us},0,{line}\n".encode()


class WirelessProtocolTests(unittest.TestCase):
    def test_exact_spacing_with_jitter_gaps_and_large_uptime(self):
        timeline = WirelessTimeline()
        start = 5_000_000_000  # beyond the old 32-bit micros() rollover
        elapsed = []
        for seq, offset in ((1, 0), (2, 10000), (3, 20500), (5, 40500)):
            stamp, rebooted = timeline.append(WirelessRecord.parse(wire_record(seq, start + offset).decode()))
            elapsed.append(stamp)
            self.assertFalse(rebooted)
        self.assertEqual(elapsed, [0, .01, .0205, .0405])
        self.assertAlmostEqual(timeline.sample_hz, 3 / .0405)
        self.assertEqual(timeline.queue_age_ms, 100)
        stamp, rebooted = timeline.append(WirelessRecord.parse(wire_record(6, 500, boot=18).decode()))
        self.assertTrue(rebooted)
        self.assertEqual(stamp, 0)

    def test_reject_bad_envelopes_and_clock_regression(self):
        valid = wire_record(1, 1000).decode()
        for line in ("T1,1", valid.replace("W1,17", "W2,17"),
                     valid.replace("17,1000", "17,-1000"),
                     valid.replace("17,1000", "17,999999999999999999999"),
                     valid.replace("17,1000,101000", "17,1000,999"),
                     valid.replace("W1,17", "W1,4294967296")):
            with self.subTest(line=line[:50]), self.assertRaises(ValueError):
                WirelessRecord.parse(line)
        timeline = WirelessTimeline()
        timeline.append(WirelessRecord.parse(valid))
        with self.assertRaises(ValueError):
            timeline.append(WirelessRecord.parse(wire_record(2, 999).decode()))


class WirelessUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        args = argparse.Namespace(demo=False, transport="tcp", baud=115200,
                                  udp_port=14551, port=None, exit_after=None, packet_timeout=3)
        self.window = Dashboard(args)
        self.window.poll_timer.stop()
        self.window.graph_timer.stop()
        self.window.hud_page.wireless = True
        self.window.events.put(("tcp_session", "test bridge"))

    def tearDown(self):
        self.window.close()

    def test_batches_preserve_capture_timing_independent_of_pc(self):
        window = self.window
        first = b"".join(wire_record(i, 5_000_000 + i * 10_000) for i in range(10))
        with patch("scv2_dashboard.time.monotonic", return_value=90000):
            window.events.put(("wireless_packet", first[:47]))
            window.poll_events()
            self.assertEqual(len(window.graph_history), 0)
            window.events.put(("wireless_packet", first[47:]))
            window.poll_events()
        second = b"".join(wire_record(i, 5_000_000 + i * 10_000) for i in range(10, 20))
        with patch("scv2_dashboard.time.monotonic", return_value=90123):
            window.events.put(("wireless_packet", second))
            window.poll_events()
        elapsed = window.graph_history.snapshot()[0]
        self.assertEqual(len(elapsed), 20)
        for i, value in enumerate(elapsed):
            self.assertAlmostEqual(value, i * .01)
        self.assertEqual(window.data_rate.text(), "ESP: 100.0 Hz")
        self.assertEqual(len(window.hud_page.averager.samples), 20)
        self.assertIn("delivery age unknown", window.hud_page.quality.text())
        self.assertFalse(window.command_input.isEnabled())
        self.assertFalse(window.auto_telemetry.isEnabled())
        # Rendering accumulated data never makes its watchdog receipt time newer.
        timestamp = window.hud_page.received_at
        window.hud_page.set_sample(window.last_sample, already_accumulated=True)
        self.assertEqual(window.hud_page.received_at, timestamp)

    def test_reconnect_discards_partial_line_and_preserves_same_boot_time(self):
        window = self.window
        window.events.put(("wireless_packet", wire_record(1, 1000000) + wire_record(2, 1010000)[:35]))
        window.poll_events()
        window.events.put(("tcp_retry", "retrying"))
        window.poll_events()
        self.assertTrue(window.hud_page.bars.stale)
        window.events.put(("tcp_session", "test bridge"))
        window.events.put(("wireless_packet", wire_record(3, 1020000)))
        window.poll_events()
        self.assertEqual(window.last_seq, 3)
        self.assertEqual(window.frame_gaps, 1)
        self.assertEqual(list(window.graph_history.snapshot()[0]), [0, .02])
        window.events.put(("wireless_packet", wire_record(4, 1000, boot=18)))
        window.poll_events()
        self.assertEqual(list(window.graph_history.snapshot()[0]), [0])
        self.assertEqual(len(window.hud_page.averager.samples), 1)

    def test_controller_reset_and_sequence_wrap(self):
        window = self.window
        for seq, timestamp in ((0xFFFFFFFE, 1000), (1, 2000), (0, 3000), (1, 4000)):
            window.events.put(("wireless_packet", wire_record(seq, timestamp)))
        window.poll_events()
        self.assertEqual(window.frame_gaps, 2)
        self.assertEqual(len(window.graph_history), 4)
        self.assertEqual(len(window.hud_page.averager.samples), 2)

    def test_plain_t1_is_rejected_on_timestamped_tcp(self):
        self.window.events.put(("wireless_packet", b"T1,1,2\n"))
        self.window.poll_events()
        self.assertEqual(len(self.window.graph_history), 0)
        self.assertIn("update the ESP", self.window.connection.text())


class TcpReaderTests(unittest.TestCase):
    def test_loopback_fragmentation_disconnect_and_reconnect(self):
        events = queue.Queue()
        failures = []
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(2)
            listener.settimeout(3)
            payloads = [wire_record(1, 1000) + b"W1,17,", wire_record(3, 21000)]
            def bridge():
                try:
                    for payload in payloads:
                        connection, _ = listener.accept()
                        with connection:
                            connection.sendall(payload[:7])
                            connection.sendall(payload[7:])
                except Exception as exc:
                    failures.append(exc)
            server = threading.Thread(target=bridge, daemon=True)
            server.start()
            reader = TcpReader("127.0.0.1", listener.getsockname()[1], events)
            reader.start()
            sessions = []
            deadline = time.monotonic() + 5
            try:
                while time.monotonic() < deadline:
                    message = events.get(timeout=3)
                    if message[0] == "tcp_session":
                        sessions.append(bytearray())
                    elif message[0] == "wireless_packet":
                        sessions[-1].extend(message[1])
                    if len(sessions) == 2 and sessions[1] == payloads[1]:
                        break
            finally:
                reader.stop()
                reader.join(2)
                server.join(2)
            self.assertFalse(reader.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(sessions, payloads)

    def test_full_ui_queue_is_cancellable(self):
        events = queue.Queue(maxsize=1)
        events.put(("busy", None))
        reader = TcpReader("127.0.0.1", 1, events)
        writer = threading.Thread(target=reader._emit, args=(("test", None),))
        writer.start()
        reader.stop()
        writer.join(1)
        self.assertFalse(writer.is_alive())

    def test_stop_on_full_queue_retains_terminal_event(self):
        events = queue.Queue(maxsize=1)
        events.put(("wireless_packet", b"partial"))
        reader = TcpReader("127.0.0.1", 1, events)
        reader.stop()
        reader.start()
        reader.join(1)
        self.assertFalse(reader.is_alive())
        self.assertEqual(events.get_nowait()[0], "disconnected")


if __name__ == "__main__":
    unittest.main()
