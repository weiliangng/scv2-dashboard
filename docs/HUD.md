# HUD tab

The HUD shares the dashboard's existing T1 connection and demo mode. It sends
no commands. Yellow is estimated usable capacitor energy; the overlay is
potential charging (green, right) or discharging (red, left). The independent
lower bar shows measured converter output current, positive when charging.

## Sources and scales

`src/hud_config.py` centralizes HUD scales, colours and geometry. Defaults follow
SCV2 `Core/Inc/app_constants.h` and `knowledge-base/legacy-website.md`:

- Estimated bank capacitance: 5 F (`SCAP_ENERGY_ESTIMATED_CAPACITANCE_F`).
- Nominal ceiling: 26.3 V; discharge floor: 20% × 26.3 = 5.26 V.
  The 10 V UVLO constant applies to the bus, not this capacitor-energy floor.
- Usable energy: `0.5 × 5 × (Vcap² − 5.26²)`, clamped to 0–1660.056 J.
  This is an estimate, not the firmware's integrated `cap_energy_mJ` counter.
- Potential overlay: ±240 W; full scale occupies 30% of the energy span.
- Measured OUT current: ±15 A. The ±10 A IN/current-demand clamp is a
  different quantity and is not used for this measured-current strip.

The energy scale stays fixed at the nominal window; the runtime charge ceiling
(`vcap_max_mV`) is shown separately, including firmware derates. Raw voltage and
current remain visible outside the display range. Over-range bars saturate and
show a diagnostic. Space on both sides preserves the power overlay at 0/100%.

## Referee power and independent current

Potential power = fresh referee power limit − chassis load. In EXTERNAL mode,
fresh, valid UART power **and energy** take priority; otherwise a fresh valid
CAN power command is used, matching `ScapIo_Resolve1kHz`. CAN's disabled-energy
sentinel still permits its power budget. Other modes and missing sources show
potential as unavailable. Safety/SWEN inhibition does not erase potential power;
actual flow remains independently visible on the current bar.

The burst controller can change `pset_W`, so it is not the referee budget.
CAN and UART power/energy inputs are shown separately with validity/freshness,
including disabled CAN energy (777). Zero measured current says no measured
flow; the separate SWEN textbox indicates whether switching is off.

## Relationship to CAN 0x077

The dashboard reads T1 over USB/UART/UDP, not raw CAN. The firmware's
`CAN_TELEMETRY.md` defines an **8-byte**, little-endian frame:

| Bytes | Quantity | HUD source |
|---|---|---|
| 0–1 | Unsigned load, 0.1 W | T1 `vb_mV × il_mA / 1e6`, explicitly labelled |
| 2–3 | Unsigned capacitor voltage, 0.1 V | T1 `vc_mV / 1000` |
| 4–5 | Signed measured output current, 0.1 A | T1 `io_mA / 1000` |
| 6 | Reserved, currently zero | Documented, no measurement invented |
| 7 | Vbus OVP, Vcap OVP, CAN command fresh | T1 fault bits 0/1 and CAN validity/freshness |

CAN load uses the firmware's averaged `g_latest.p_chassis`, with unsigned
clamping and 0.1 W quantization. T1 has no averaged-load field: the HUD's signed
instantaneous product is an approximation, not a byte-exact CAN reconstruction.
The status textbox is explicitly labelled as reconstructed from T1; it is not
a captured CAN frame. Power_limit and energy_buffer are incoming command data,
not additional fields in 0x077. T1's positional schema is unchanged.

After one second without a valid T1 update, the HUD holds the last readings,
marks them STALE and mutes the bars/textboxes. Malformed records and unrelated
CLI output do not refresh it. A new connection clears the HUD.

## Verification

```powershell
.\.venv\Scripts\python.exe .\src\scv2_dashboard.py --self-test
.\.venv\Scripts\python.exe -m unittest discover -s tests
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe .\src\scv2_dashboard.py --demo --exit-after 2
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build-dashboard-exe.ps1
```

For an interactive preview, run with `--demo` and select HUD. The existing demo
is simulated T1, not a physical energy simulation. Run without `--demo` for live
telemetry. See SETUP.md for connection and packaging instructions.
