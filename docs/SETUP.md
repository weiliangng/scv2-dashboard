# Dashboard setup, operation, and troubleshooting

This guide covers the desktop application owned by this repository. The
[SCV2 firmware repository](https://github.com/weiliangng/scv2) remains
authoritative for the `T1` field semantics, USB CLI commands, telemetry timing,
and physical USART1 interface. See [PROTOCOL.md](PROTOCOL.md) for the contract
the dashboard currently implements.

Before connecting hardware, a human must confirm the actual pin-to-signal
wiring, logic voltage, and common ground. Do not infer wiring from this guide.

## Run the released application

Download `SCV2-Dashboard.exe` and `SCV2-Dashboard.exe.sha256` from the
[latest release](https://github.com/weiliangng/scv2-dashboard/releases/latest).
The executable runs on 64-bit Windows 10 or 11 without a separate Python, Qt,
or CMake installation. It is unsigned, so follow the organisation-approved
Windows SmartScreen review process.

Keep the checksum beside the executable. To inspect both values in PowerShell:

```powershell
Get-FileHash -LiteralPath .\SCV2-Dashboard.exe -Algorithm SHA256
Get-Content -LiteralPath .\SCV2-Dashboard.exe.sha256
```

The two SHA-256 values must match. Different builds of the same source are not
guaranteed to have identical hashes; compare against the checksum published
with the specific executable being used.

Only one application can normally own a COM port. Close other dashboard
instances, serial terminals, and IDE serial monitors before connecting.

## Run from source

The pinned development environment is tested with 64-bit Python 3.14. From the
repository root, install `uv` and run:

```powershell
uv venv --python 3.14 .venv
uv pip install --python .\.venv\Scripts\python.exe -r .\requirements-build.txt
.\.venv\Scripts\python.exe .\src\scv2_dashboard.py --self-test
.\.venv\Scripts\python.exe .\src\scv2_dashboard.py --demo
```

A conventional Python installation also works:

```powershell
py -V:3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\requirements-build.txt
```

There is no need to activate the environment. The direct Python command always
runs the source. `Start SCV2 Dashboard.cmd` runs the root executable first when
one exists and falls back to the source environment only when it does not.

Useful source options include:

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

Options preselect controls but do not press **Connect** automatically.

## Connect a telemetry source

The values below describe the contract currently consumed by the dashboard;
confirm firmware-controlled interface details in
[PROTOCOL.md](PROTOCOL.md) and the SCV2 firmware documentation.

### Board USB CDC

1. Select **USB serial** and refresh the port list.
2. Select the SCV2 COM port. The displayed 115200 baud is a host convention;
   USB CDC does not use it as a physical baud rate.
3. Leave **Enable USB telemetry while connected** selected.
4. Select **Connect**.

The application requests `telemetry on` after connecting and `telemetry off`
before disconnecting.

### External serial receiver

1. Connect a receive-only adapter using the firmware-approved wiring.
2. Select **USB serial**, the adapter COM port, and `921600` baud.
3. Clear **Enable USB telemetry while connected** because this transport cannot
   accept USB CLI commands.
4. Select **Connect**.

The dashboard must not be used to infer the MCU pin, signal voltage, or ground
connection. Those are hardware/firmware-owned facts.

### ESP Serial Bridge UDP

1. Configure the bridge to forward the required UART stream to this PC.
2. Select **UDP listener** and the configured local port; the current UART1
   convention is `14551`.
3. Select **Connect** and allow the application through Windows Firewall for
   the appropriate network profile if prompted.

The dashboard binds and listens. It performs no discovery, sends no reply, and
reassembles newline-delimited records across arbitrary datagram boundaries.

## USB CLI tab

The USB CLI tab is enabled only for a board USB CDC connection when automatic
USB telemetry is selected. For each command, the application pauses its own
telemetry mirror, waits for the `scv2> ` prompt, sends one command, waits for
the next prompt, and restores telemetry. It uses a five-second prompt timeout.

The dashboard does not restrict firmware commands. Commands such as `ctrl`,
`gpio`, `dac`, and `cal` can change hardware state. Consult the firmware's
[CLI guide](https://github.com/weiliangng/scv2/blob/main/CLI_GUIDE.md) and use a
safe, current-limited bench setup.

## Build the Windows executable

Install `requirements-build.txt`, close any running copy of the root
executable, and run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build-dashboard-exe.ps1
```

The script creates `SCV2-Dashboard.exe` in the repository root and waits for
its packaged self-test. PyInstaller output and executables are ignored by Git.
Build on 64-bit Windows for 64-bit Windows targets; PyInstaller does not
cross-compile the Windows application.

## Publish a dashboard release

Dashboard releases are independent of SCV2 firmware releases. After committing
and pushing a tested dashboard change, push a version tag such as `v1.2.0`:

```powershell
git tag -a v1.2.0 -m "SCV2 Dashboard v1.2.0"
git push origin v1.2.0
```

The release workflow tests and packages the dashboard on a clean Windows
runner, publishes `SCV2-Dashboard.exe`, and publishes its matching checksum.

## Troubleshooting

### Python, environment, or imports are missing

- Run commands from the repository root.
- Confirm `.\.venv\Scripts\python.exe --version` reports Python 3.14.
- Recreate `.venv` and reinstall `requirements-build.txt` if imports such as
  `PySide6`, `numpy`, `pyqtgraph`, or `serial` fail.
- Do not substitute an unrelated global `pip` executable.

### COM port is missing or access is denied

- Confirm the cable supports data and inspect Windows Device Manager.
- Close every other program that might own the port.
- Reconnect the device and select **Refresh ports**.

### Packets arrive but no values appear

- Confirm the firmware emits the exact 69-field `T1` schema in
  [PROTOCOL.md](PROTOCOL.md); legacy 68-field records are rejected.
- Use the connection message to inspect malformed-record errors.
- For USB CDC, enable automatic USB telemetry.
- For a receive-only serial stream, disable automatic USB telemetry.

### UDP receives nothing

- Confirm the bridge targets this PC's address and the selected UDP port.
- Ensure no other application has bound the port.
- Check the Windows Firewall network profile.
- Confirm the bridge and PC are on the intended network.

### Install dependencies offline

On an internet-connected Windows PC using the same Python version and CPU
architecture, download the pinned wheels:

```powershell
py -V:3.14 -m pip download -r .\requirements-build.txt -d .\dashboard-wheels
```

Copy `dashboard-wheels`, create `.venv` on the offline PC, and install only
from that directory:

```powershell
.\.venv\Scripts\python.exe -m pip install --no-index --find-links .\dashboard-wheels -r .\requirements-build.txt
```
