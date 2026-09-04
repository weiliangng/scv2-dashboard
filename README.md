# SCV2 Dashboard

Desktop telemetry and USB CLI client for the SCV2 supercapacitor controller.
The application accepts SCV2 `T1` telemetry over USB serial, a receive-only
UART adapter, or the ESP Serial Bridge's UDP stream.

## Run the released application

Download `SCV2-Dashboard.exe` and `SCV2-Dashboard.exe.sha256` from the latest
GitHub Release. Verify the checksum, then run the EXE on 64-bit Windows 10 or
11. It bundles Python, Qt, pyserial, NumPy, and PyQtGraph. The unsigned in-house
executable may show a Windows SmartScreen warning.

Only one application can own a COM port at a time. Close serial terminals and
other dashboard instances before connecting.

## Run from source

Install `uv`, then run from the repository root:

```powershell
uv venv --python 3.14 .venv
uv pip install --python .\.venv\Scripts\python.exe -r .\requirements-build.txt
.\.venv\Scripts\python.exe .\src\scv2_dashboard.py --self-test
& ".\Start SCV2 Dashboard.cmd"
```

A conventional Python 3.14 installation also works: create `.venv` with
`python -m venv .venv`, then install `requirements-build.txt` with the virtual
environment's `python -m pip`.

Useful options:

```text
--port COM8
--baud 115200
--transport serial|udp
--udp-port 14551
--packet-timeout 3.0
--demo
--exit-after SECONDS
--self-test
```

Options preselect the UI controls; they do not automatically connect.

## Live graphs

The Graphs tab uses a 30-second oscilloscope-style sweep in a 2-by-2 layout.
At the end of each pass, the plotted history is cleared and a new trace begins
at zero. It redraws only while visible, at no more than 30 Hz, and plots virtual
CAN/UART energy, capacitor voltage, chassis power, capacitor power, and
requested power. PyQtGraph clips data outside the visible time range and uses
peak-preserving automatic downsampling when zoomed out.

The vertical axes are fixed so changing values do not rescale the plots:
virtual energy is 0 to 70 J, capacitor voltage is 0 to 30 V, chassis power is
-50 to 400 W, and the combined capacitor/requested-power plot is -260 to 260 W.

Virtual energy is shown only when the active control decision is CAN or UART
and the corresponding energy value is valid and fresh. An unavailable value
creates a gap rather than being displayed as zero.

## Connections

- Board USB CDC: select USB serial. The displayed 115200 baud value is a host
  convention; USB CDC does not use it as a physical baud rate. With automatic
  USB telemetry enabled, the dashboard sends `telemetry on` while connected.
- External UART receiver: SCV2 USART1 transmits at 921600 baud, 8-N-1. This is
  receive-only and does not support USB CLI commands.
- ESP Serial Bridge: listen for raw UART1 bytes on UDP port 14551. The dashboard
  reconstructs newline-delimited records across arbitrary datagram boundaries.

Before hardware testing, a human must confirm the actual pin-to-signal wiring,
logic voltage, and common ground. Do not infer the physical connection from
repository configuration alone.

## Build the Windows executable

After installing `requirements-build.txt` into `.venv`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build-dashboard-exe.ps1
```

The script creates `SCV2-Dashboard.exe` in the repository root and runs its
built-in self-test. Generated executables and PyInstaller directories are
ignored by Git.

## Releases

Push a version tag such as `v1.1.0`. GitHub Actions tests and packages the
dashboard on a clean Windows runner, produces a SHA-256 checksum, and creates
or updates the matching GitHub Release. Pull requests and pushes to `main` run
the source, UI, and packaged executable checks without publishing a release.

Historical `dashboard-v1.0.x` tags and releases remain in
[weiliangng/scv2](https://github.com/weiliangng/scv2).

See [docs/PROTOCOL.md](docs/PROTOCOL.md) for the firmware-facing contract.
