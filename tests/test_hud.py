"""Model, geometry and real Qt integration checks; no device connection."""

import argparse
from dataclasses import replace
import math
import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6 import QtGui, QtWidgets
from hud import HudPage, bar_geometry, hud_values
from hud_config import HUD_CONFIG, HudConfig
from scv2_dashboard import Dashboard, FIELDS, demo_sample


def sample(**changes):
    return {**demo_sample(0), "vb_mV": 24000, "il_mA": 10000,
            "vc_mV": 26300, "io_mA": -2500, **changes}


class HudModelTests(unittest.TestCase):
    def test_voltage_energy_and_limits(self):
        self.assertAlmostEqual(HUD_CONFIG.capacity_j, 1660.056)
        self.assertEqual(hud_values(sample(vc_mV=5260)).fraction, 0)
        self.assertEqual(hud_values(sample(vc_mV=-30000)).fraction, 0)
        self.assertEqual(hud_values(sample(vc_mV=30000)).fraction, 1)
        half_v = math.sqrt((26.3**2 + 5.26**2) / 2)
        self.assertAlmostEqual(hud_values(sample(vc_mV=round(half_v * 1000))).fraction, .5, places=4)
        self.assertEqual(hud_values(sample(vcap_max_mV=23000)).fraction, 1)

    def test_independent_quantities(self):
        v = hud_values(sample())
        self.assertEqual((v.potential_w, v.current_a), (-160, -2.5))
        self.assertEqual(hud_values(sample(il_mA=0)).potential_w, 80)
        self.assertEqual(hud_values(sample(pset_W=0)).potential_w, -160)
        self.assertEqual(hud_values(sample(io_mA=4000)).energy_j, v.energy_j)
        self.assertEqual(hud_values(sample(io_mA=4000)).potential_w, v.potential_w)
        self.assertEqual(hud_values(sample(can_p=120)).current_a, v.current_a)
        self.assertEqual(hud_values(sample(swen_out=0)).current_a, -2.5)

    def test_sources_and_status(self):
        self.assertIsNone(hud_values(sample(can_p_fresh=0)).potential_w)
        self.assertIsNone(hud_values(sample(can_p_valid=0)).potential_w)
        self.assertIsNone(hud_values(sample(mode_req=1)).potential_w)
        self.assertEqual(hud_values(sample(can_e_disabled=1, can_e_valid=0, can_e=777)).budget_w, 80)
        uart = sample(uart_p=100, uart_p_valid=1, uart_p_fresh=1,
                      uart_e_valid=1, uart_e_fresh=1)
        self.assertEqual(hud_values(uart).budget_source, "UART")
        self.assertEqual(hud_values({**uart, "uart_e_fresh": 0}).budget_source, "CAN")
        self.assertEqual(hud_values(sample(fault_bits=3)).status, 7)
        self.assertEqual(hud_values(sample(fault_bits=0x80, can_p_fresh=0)).status, 0)
        # Safety inhibition must not hide available potential power.
        self.assertEqual(hud_values(sample(decision=0, swen_out=0)).potential_w, -160)

    def test_geometry_and_overflow(self):
        full = bar_geometry(1000, 1, 50, 0)
        self.assertGreater(full["power_left"] + full["power_width"], full["origin"] + full["span"])
        low = bar_geometry(1000, .1, -240, -15)
        self.assertLess(low["power_left"], low["origin"])
        empty = bar_geometry(1000, 0, -240, -15)
        self.assertAlmostEqual(empty["power_left"], 0)
        self.assertEqual(empty["current_left"], empty["origin"])
        over = bar_geometry(1000, .1, -480, -30)
        self.assertEqual(low, over)
        self.assertEqual(bar_geometry(1000, .8, 80, 0)["current_width"], 0)
        narrower = bar_geometry(1000, .1, -240, -15, replace(HUD_CONFIG, current_full_scale_a=30))
        self.assertEqual(narrower["power_width"], low["power_width"])
        self.assertEqual(narrower["current_width"], low["current_width"] / 2)

    def test_config_validation(self):
        for changes in ({"current_full_scale_a": 0}, {"cap_cutoff_v": 30},
                        {"bank_capacitance_f": math.nan}, {"power_overlay_span_ratio": 2}):
            with self.assertRaises(ValueError):
                HudConfig(**changes)


class HudUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app.setStyle("Fusion")
        # Windows' offscreen plugin does not discover installed fonts itself.
        font = Path("C:/Windows/Fonts/segoeui.ttf")
        if font.exists():
            QtGui.QFontDatabase.addApplicationFont(str(font))
            cls.app.setFont(QtGui.QFont("Segoe UI", 10))

    def test_missing_stale_and_render(self):
        page = HudPage()
        self.assertIn("Waiting", page.quality.text())
        page.set_sample(sample(), received_at=10)
        page.refresh_quality(10.5)
        self.assertFalse(page.bars.stale)
        page.refresh_quality(11)
        self.assertTrue(page.bars.stale)
        self.assertEqual(page.bars.values.current_a, -2.5)
        page.set_sample(sample(io_mA=0, swen_out=1))
        self.assertIn("no measured flow", page.current.text())
        self.assertIn("ON", page.boxes["control"].text())
        page.resize(750, 1100)
        page.show()
        self.app.processEvents()
        self.assertFalse(page.grab().isNull())
        page.resize(420, 700)
        self.app.processEvents()
        self.assertFalse(page.grab().isNull())
        if QtGui.QFontDatabase.families():
            self.assertEqual(page.horizontalScrollBar().maximum(), 0)
        page.reset()
        self.assertIsNone(page.bars.values)
        self.assertEqual(page.boxes["load"].text(), "—")
        page.close()

    def test_dashboard_stream_integration(self):
        args = argparse.Namespace(demo=False, transport="serial", baud=115200,
                                  udp_port=14551, port=None, exit_after=None, packet_timeout=3)
        window = Dashboard(args)
        window.poll_timer.stop()
        window.graph_timer.stop()
        window.tabs.setCurrentWidget(window.hud_page)
        data = sample()
        line = ("T1," + ",".join(str(data[f.name]) for f in FIELDS) + "\r\n").encode()
        window.events.put(("packet", line[:100]))
        window.poll_events()
        self.assertIsNone(window.hud_page.received_at)
        window.events.put(("packet", line[100:]))
        window.poll_events()
        self.assertEqual(window.hud_page.bars.values.potential_w, -160)
        timestamp = window.hud_page.received_at
        window.events.put(("packet", b"T1,bad\nCLI ready\n"))
        window.poll_events()
        self.assertEqual(window.hud_page.received_at, timestamp)
        window.hud_page.refresh_quality(timestamp + 2)
        self.assertTrue(window.hud_page.bars.stale)
        window.show()
        self.app.processEvents()
        self.assertFalse(window.grab().isNull())
        window.close()


if __name__ == "__main__":
    unittest.main()
