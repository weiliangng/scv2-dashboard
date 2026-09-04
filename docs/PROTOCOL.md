# SCV2 dashboard protocol

The SCV2 firmware remains authoritative for the bytes it emits. This document
records the contract implemented by this dashboard so compatibility changes can
be coordinated between repositories.

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
- ESP Serial Bridge UART1: 921600 baud and UDP port 14551.
- UDP receiver: binds all local interfaces, performs no discovery, and sends no
  reply.

The original firmware contract is maintained in the
[SCV2 firmware repository](https://github.com/weiliangng/scv2).
