# Development guidance

- Keep the `T1` field order in `src/scv2_dashboard.py` synchronized with
  `docs/PROTOCOL.md` and the SCV2 firmware.
- Treat changes to the number, order, units, or meaning of telemetry fields as
  protocol changes. Do not silently reinterpret `T1`.
- This repository owns desktop setup, UI behavior, packaging, and dashboard
  releases. The SCV2 repository owns firmware behavior, hardware interfaces,
  USB CLI command semantics, and the emitted telemetry schema.
- Run the source self-test, headless UI smoke test, Windows package build, and
  packaged self-test for dashboard changes.
- Do not commit virtual environments, PyInstaller output, executables, or
  checksums.
- USB CLI commands can control physical hardware. Do not infer wiring; require
  the human to specify which physical pin connects to each signal or device.
