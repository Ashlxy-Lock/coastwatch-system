"""OpenMV entry point for the coastal warning vision node."""

import sensor
import time

import config
import protocol
import vision_detector


def _init_sensor():
    sensor.reset()

    frame_size = getattr(sensor, config.FRAME_SIZE_NAME)
    if config.VISION_MODE == config.MODE_FACE_DETECTION:
        sensor.set_pixformat(sensor.GRAYSCALE)
        sensor.set_contrast(3)
        sensor.set_gainceiling(16)
    elif config.VISION_MODE in (
        config.MODE_COLOR_MARKER_DEMO,
        config.MODE_PERSON_CLASSIFIER,
    ):
        sensor.set_pixformat(sensor.RGB565)
    else:
        raise RuntimeError(
            "camera setup is not implemented for " + str(config.VISION_MODE)
        )

    sensor.set_framesize(frame_size)
    sensor.set_vflip(config.VERTICAL_FLIP)
    sensor.set_hmirror(config.HORIZONTAL_MIRROR)
    sensor.skip_frames(time=config.CAMERA_WARMUP_MS)

    if config.VISION_MODE == config.MODE_COLOR_MARKER_DEMO:
        # Let automatic exposure/white balance settle first, then lock them so
        # the calibrated LAB threshold remains stable.
        sensor.set_auto_gain(False)
        sensor.set_auto_whitebal(False)
        sensor.skip_frames(time=config.COLOR_LOCK_SETTLE_MS)


def _init_uart():
    if not config.UART_ENABLED:
        return None

    try:
        try:
            from pyb import UART
        except ImportError:
            from machine import UART

        return UART(
            config.UART_ID,
            config.UART_BAUD,
            timeout_char=config.UART_TIMEOUT_CHAR_MS,
        )
    except Exception as error:
        raise RuntimeError("UART initialization failed: " + str(error))


def run():
    _init_sensor()
    detector = vision_detector.create_detector()
    uart = _init_uart()

    print("VISION_READY board=%s mode=%s uart=%s" % (
        config.BOARD_MODEL,
        detector.mode_name,
        "on" if uart is not None else "off",
    ))

    sequence = 0
    last_send_ms = time.ticks_ms()
    last_fps_ms = time.ticks_ms()
    clock = time.clock()
    frame_size_verified = False

    while True:
        clock.tick()
        frame = sensor.snapshot()
        if not frame_size_verified:
            if (
                frame.width() != config.FRAME_WIDTH
                or frame.height() != config.FRAME_HEIGHT
            ):
                raise RuntimeError(
                    "configured frame dimensions do not match FRAME_SIZE_NAME"
                )
            frame_size_verified = True

        result = detector.detect(frame)

        if config.DEBUG_DRAW:
            vision_detector.draw_debug(frame, result)

        now_ms = time.ticks_ms()
        if time.ticks_diff(now_ms, last_send_ms) >= config.SEND_INTERVAL_MS:
            encoded = protocol.encode_vis(
                sequence,
                result[0],
                result[1],
                result[2],
                result[3],
                result[4],
            )
            if uart is not None:
                uart.write(encoded)
            if config.DEBUG_PRINT_FRAMES:
                print(encoded.strip())
            sequence = protocol.next_sequence(sequence)
            last_send_ms = now_ms

        if (
            config.DEBUG_PRINT_FPS_EVERY_MS > 0
            and time.ticks_diff(now_ms, last_fps_ms)
            >= config.DEBUG_PRINT_FPS_EVERY_MS
        ):
            print("FPS=%.2f" % clock.fps())
            last_fps_ms = now_ms


try:
    run()
except Exception as error:
    # Do not emit fake "no target" frames after a fatal camera/detector error.
    # When STM32 is connected later, the missing VIS heartbeat must become FAULT.
    print("VISION_FATAL:", error)
    raise
