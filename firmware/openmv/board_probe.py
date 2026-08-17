s"""First-connection probe for MV4/OpenMV H7 Plus.

Open this single file in OpenMV IDE and press Run. It does not write to flash,
upgrade firmware, or use the hardware UART.
"""

import sensor
import time
import uos


print("BOARD_PROBE_SYSTEM:", uos.uname())

sensor.reset()
sensor.set_pixformat(sensor.RGB565)
sensor.set_framesize(sensor.QVGA)
sensor.skip_frames(time=2000)

clock = time.clock()
last_report_ms = time.ticks_ms()

print("BOARD_PROBE_CAMERA_READY")

while True:
    clock.tick()
    frame = sensor.snapshot()
    now_ms = time.ticks_ms()

    if time.ticks_diff(now_ms, last_report_ms) >= 2000:
        print(
            "BOARD_PROBE_FRAME width=%d height=%d fps=%.2f"
            % (frame.width(), frame.height(), clock.fps())
        )
        last_report_ms = now_ms

