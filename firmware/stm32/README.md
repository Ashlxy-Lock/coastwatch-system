# CoastWatch STM32 sensor bridge

This is the independently buildable STM32F103ZET6 firmware for the CoastWatch
prototype. It reads an HC-SR04-style TRIG/ECHO ultrasonic sensor, receives
OpenMV `VIS` frames, sends `TEL` frames to the ESP32 every 500 ms, and receives
ESP32 `NET` frames. The current persistent-recovery build has been compiled,
flashed through ST-Link, and verified on the board.

## Wiring

Power all boards appropriately and connect their grounds.

| Link | Source | Destination |
|---|---|---|
| Ultrasonic trigger | Sensor `TRI` / `TRIG` | STM32 `PC10` |
| Ultrasonic echo | Sensor `ECH` / `ECHO` | STM32 `PC11` |
| Ultrasonic power | Sensor `VCC`, `GND` | `5V`, common `GND` |
| OpenMV vision | OpenMV `P4 / UART3 TX` | STM32 `PB11 / USART3 RX` |
| Optional OpenMV return | STM32 `PB10 / USART3 TX` | OpenMV `P5 / UART3 RX` |
| ESP32 telemetry | STM32 `PA2 / USART2 TX` | ESP32 `GPIO8 / UART1 RX` |
| ESP32 network status | ESP32 `GPIO14 / UART1 TX` | STM32 `PA3 / USART2 RX` |

PC11 is configured only as a digital interrupt input. The direct ECHO wiring is
specific to the previously checked STM32F103ZE PC11 input and a conventional
HC-SR04 whose ECHO does not exceed 5 V. Do not move ECHO to an arbitrary pin.
A divider or level shifter remains recommended for a permanent installation.

Both UARTs use `115200 8N1`. The firmware does not remap USART2 or USART3, so
the existing PA2/PA3 and PB10/PB11 wiring stays unchanged.

## Data behavior

The ultrasonic path is non-blocking:

1. At reset, both PC10 and PC11 are high-impedance inputs with internal
   pull-downs. Startup then uses two non-blocking stages. As soon as PC11/ECHO
   is presently low, firmware first verifies that high-impedance PC10 is also
   low, preloads PC10/TRIG low, changes it to push-pull output, and immediately
   verifies that the physical pin reads low. It keeps
   TRIG low while requiring a new continuous 50 ms ECHO-low quiet window, then
   configures and enables the PC11 EXTI capture. ECHO pulses during settling
   restart only the quiet timer; they do not release TRIG. A failed TRIG drive
   check returns both pins to safe inputs and latches sensing off until reset.
2. PC10 is raised and lowered by a polling state machine; there is no delay
   loop.
3. PC11 rising/falling edges are captured through EXTI using a 1 MHz TIM2
   counter.
4. A missing edge times out after 30 ms. The firmware keeps the already-verified
   TRIG output low and continuously watches ECHO; as soon as ECHO returns low it
   schedules the next ping instead of unloading and re-arming the GPIOs.
5. Pings are spaced by 100 ms whenever ECHO is ready.

A single echo is not trusted as the reference. Three consecutive valid
distances must fit within a 20 mm span. Their median becomes the session
baseline. Subsequent values use a five-point median followed by integer
`0.8 previous + 0.2 median` smoothing:

```text
water_rise_mm = baseline_distance_mm - filtered_distance_mm
rise_rate_mm_s = change in water_rise / elapsed time
```

A single timed-out reading is treated as acoustic noise and retains the last
valid filtered value for at most one second while the 100 ms probe loop keeps
recovering. After one second without a valid echo, a `TEL` frame carries zero
for all three measurement fields and does **not** set
`HEALTH_ULTRASONIC_OK`; stale values are never presented indefinitely as live
water data. A TRIG electrical/readback fault invalidates health immediately.
After three seconds without a valid reading, the old baseline is discarded and
a new stable baseline is required.

The same fail-closed rule applies to OpenMV: a `VIS` frame older than 1000 ms
clears `HEALTH_OPENMV_OK`, clears the reported person flag, and puts the local
state into `FAULT`. Network loss only clears `HEALTH_NETWORK_OK`; it does not
stop sensing or local alarm decisions. `HEALTH_POWER_OK` remains clear because
this board has no implemented voltage monitor.

## Protocols

OpenMV to STM32:

```text
$VIS,<seq>,<person>,<score>,<cx>,<cy>,<in_zone>*<xor>\r\n
```

STM32 to ESP32 every 500 ms:

```text
$TEL,<seq>,<uptime_ms>,<distance_mm>,<water_rise_mm>,<rise_rate_mm_s>,<person>,<alarm>,<health>*<xor>\n
```

ESP32 to STM32:

```text
$NET,<wifi>,<server>,<rssi>,<unix_time>*<xor>\n
```

The checksum is the XOR of the ASCII bytes after `$` and before `*`. UART
interrupts only enqueue bytes. Framing, checksum validation, parsing, sensor
logic, and alarm logic all run outside interrupt callbacks. Overlong or invalid
frames are dropped, and the line reader resynchronizes at the next `$`.

## Local alarm boundary

The firmware calculates the documented `SAFE`, `ADVISORY`, `WARNING`,
`CRITICAL`, and `FAULT` levels using the configurable desktop-demo water
thresholds. Network state never gates this calculation.

`Coastwatch_AlarmOutput(uint8_t level)` is a weak, no-op integration hook. No
buzzer, relay, LED strip, or other sound/light output is claimed or driven by
this project. A later board-specific implementation must be electrically
reviewed before overriding the hook.

## Build and test

Dependencies are pinned by `platformio.ini`:

- PlatformIO `ststm32@19.7.0`
- STM32CubeF1 framework (resolved as 1.8.6 during verification)
- board `genericSTM32F103ZE`

Build without flashing:

```powershell
cd firmware\stm32
pio run -e stm32f103zet6
```

Run the pure-logic host tests:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\host_test.ps1
```

Verified on 2026-08-14:

```text
STM32 build: SUCCESS
RAM:   1,948 / 65,536 bytes (3.0%)
Flash: 14,232 / 524,288 bytes (2.7%)
Host tests: All STM32 pure-logic host tests passed.
```

The host suite covers both startup stages, the post-claim ECHO quiet window,
stuck-high recovery, transient timeout grace, immediate hardware-fault
invalidation, stable-baseline acquisition, rejection of unstable echoes,
stale/timeout zeroing, health bits, vision timeout, network independence,
32-bit tick wraparound, alarm policy, checksum enforcement, line resync, and
wire compatibility with the existing ESP32 `TEL` parser.

Build output is under `.pio/build/stm32f103zet6/firmware.bin`. Do not run an
upload target until the user explicitly chooses to flash and an ST-Link or
supported serial bootloader is physically available.

## First hardware check after flashing

1. Keep the sensor facing a fixed target between 2 cm and 4 m.
2. Confirm PC10 is claimed and driven low as soon as ECHO is sampled low, then
   remains low for a fresh 50 ms ECHO-quiet window. If PC10 readback fails,
   sensing must stay disabled until reset rather than retrying against a
   possible wiring conflict.
3. Confirm three stable echoes establish the baseline; during warm-up the
   ultrasonic health bit must remain clear.
4. Confirm ESP32 logs receive one valid `TEL` frame every 500 ms.
5. Move the target closer: distance should decrease and `water_rise_mm` should
   become positive.
6. Disconnect ECHO: short acoustic misses may retain the last valid value, but
   after one second the frame must contain zero measurement fields, `alarm=4`,
   and ultrasonic health bit 0. Reconnection must recover without a reset.
7. Disconnect Wi-Fi: sensing and local alarm computation must continue.

GPIO readback is not current sensing: it cannot make a PC10-to-5 V miswire
safe. The confirmed TRI/TRIG wiring remains mandatory, and a 1 kOhm to 4.7 kOhm
series resistor on TRIG is recommended for a durable prototype.

This remains a research/demo instrument, not a certified coastal warning or
life-safety device.
