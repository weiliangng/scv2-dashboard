# SCV2 dashboard protocol

The SCV2 firmware remains authoritative for the bytes it emits. This document
records the contract implemented by this dashboard so compatibility changes can
be coordinated between repositories.

## Ownership boundary

- The [SCV2 firmware repository](https://github.com/weiliangng/scv2) owns the
  emitted `T1` schema and semantics, telemetry cadence, USB CLI commands, and
  physical USART1 interface.
- The ESP Serial Bridge repository owns UART capture, wireless envelopes,
  buffering, batching, and network configuration.
- This repository owns record validation and buffering, dashboard calculations
  and presentation, USB CLI transaction handling, desktop setup, packaging,
  and dashboard releases.

Firmware- and bridge-controlled values below are recorded as compatibility
requirements, not redefined by this repository.

## T1 telemetry

The input is an ASCII, newline-delimited CSV record beginning with `T1`,
followed by exactly 69 positional base-10 integers. Firmware normally terminates
records with CRLF and emits approximately one record every 10 ms. The dashboard
tolerates missing records but rejects incomplete records, non-integer values,
and legacy 68-field records. Unrelated CLI output is ignored.

The fields after `T1`, in order, are:

```text
seq, adc_hz, usb_drop, dma_last, dma_max,
adc_vcap, adc_vbus, adc_iload, adc_iop, adc_ion,
vc_mV, vb_mV, il_mA, iop_mA, ion_mA, io_mA, ic_mA, pset_W,
btn_in, dir_out, swen_out, mode_out, rvsoff_out, nsil_out, led_out,
dac1_ch1, dac1_ch2, dac3_ch1, dac3_ch2,
mode_req, decision, swen_req, safe, uvlo, fault_latched, fault_bits,
fault_healthy_ms, can_bus, can_p, can_p_valid, can_p_fresh,
can_e, can_e_valid, can_e_fresh, can_e_disabled,
can_swen, can_swen_valid, can_swen_fresh,
uart_p, uart_p_valid, uart_p_fresh, uart_e, uart_e_valid, uart_e_fresh,
uart_swen_req, man_p, man_p_valid, man_swen, man_swen_valid,
btn_swen, btn_swen_valid, cap_energy_mJ, vcap_max_mV,
cap_unhealthy, cap_bad_windows, cap_derates, cap_dE_mJ_min,
cap_dV_mV_min, can_tx_enqueue_fail
```

The decoder and display units are defined alongside `FIELDS` in
`src/scv2_dashboard.py`. An incompatible future format should use a new record
prefix such as `T2`; it must not silently change `T1`.

## Timestamped wireless TCP (`W1`)

The new bridge listens on `192.168.4.1:8881` (UART1) and supports one dashboard
client. It batches complete records every 100 ms. TCP is a byte stream: one
receive may contain part of a line, several lines, or portions of several
batches. LF delimits records, never TCP write/receive boundaries.

```text
W1,<boot_id>,<capture_us>,<prepared_us>,<bridge_drops>,T1,<69 unchanged integers>\n
```

All envelope numbers are unsigned decimal. `boot_id` is a 32-bit random ESP
boot identifier; `capture_us` and `prepared_us` are 64-bit monotonic ESP timer
values. `capture_us` records completion of a UART line in the UART receive
callback. `prepared_us` records assembly into the outgoing batch, **not** TCP
acknowledgment or PC delivery. `bridge_drops` is a wrapping 32-bit cumulative
counter of queue/oversize/age drops and UART error events. A UART error can
lose multiple records, so this is diagnostic, not an exact loss total. Data
discarded on client attach or abandoning a connection is not counted there;
`T1.seq` gaps are the end-to-end indicator when sequence continuity exists.

The nested `T1` record has exactly the existing schema. Only TCP requires `W1`;
USB, direct serial and legacy UDP still use unwrapped `T1`.

The dashboard subtracts the first capture timestamp using integer arithmetic,
then converts microseconds to seconds. Graph spacing and **ESP: … Hz** depend
only on received ESP timestamps, including real jitter and missing samples.
It does not force `seq * 10 ms`, spread records across PC arrival intervals, or
map ESP time onto the PC clock. The rate is received-record intervals divided
by their capture-time span over approximately the most recent ESP second; it
is not the network packet rate. Missing records reduce that rate.

Automatic TCP reconnect resets the byte parser but preserves the timeline for
the same ESP boot. A changed boot ID resets graph history, rate history, and
HUD averaging. A backwards timestamp within one boot is rejected. A controller
sequence reset clears HUD averaging without resetting the ESP clock; normal
32-bit sequence wrap is handled as wrap. Manual Connect starts a new session.

The ESP's 8 KiB UART RX buffer and UART callback isolate capture from network
backpressure. A 64-record queue drops oldest data when full; records older than
500 ms are discarded before batching. An application batch that cannot be
fully submitted to the socket within 500 ms closes the connection. These are
application buffer limits, **not** a maximum end-to-end latency guarantee:
TCP can still delay bytes already accepted by its own buffers. Reconnect does
not replay unacknowledged data from a previous connection.

PC monotonic time is used only for receipt/connection watchdogs in TCP mode.
The HUD reports receiving or stale/holding; absolute delivery age is unknown
without synchronizing clocks. `prepared_us - capture_us` is only ESP queue
age. Capture timestamps include UART transmission and callback scheduling;
exact MCU acquisition time would require an MCU timestamp in a future schema.

## USB CLI transaction

USB CDC is the only command-capable transport. The dashboard writes commands
with CRLF, recognizes the exact prompt `scv2> ` as command completion, and uses
a five-second prompt timeout. When it enabled the USB telemetry mirror, one
dashboard command is executed as:

```text
telemetry off -> wait for prompt -> requested command -> wait for prompt -> telemetry on
```

The dashboard does not locally restrict firmware commands. Commands including
`ctrl direct`, `gpio`, `dac`, and `cal` can affect physical hardware.

## Transport values

- USB CDC: conventional host setting 115200 baud, bidirectional.
- SCV2 USART1: 921600 baud, 8-N-1, always-on telemetry, receive-only client.
- ESP Serial Bridge UART1: 921600 baud, timestamped TCP port 8881, 100 ms batches.
- Legacy ESP Serial Bridge firmware: raw UDP port 14551.
- TCP receiver: connects to the selected bridge host and port, sends no UART
  commands, retries after 500 ms, and reconnects after the packet timeout
  (default 3 seconds) without received bytes.
- UDP receiver: binds all local interfaces, performs no discovery, and sends no
  reply.

The original firmware contract is maintained in the
[SCV2 firmware repository](https://github.com/weiliangng/scv2).
