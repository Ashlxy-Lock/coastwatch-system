"""OpenMV entry point for the coastal warning vision node."""

import sensor
import time

import config
import control
import protocol
import vision_detector


_status_leds = []


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


def _init_status_leds():
    try:
        from pyb import LED

        danger_led = LED(config.DANGER_LED_ID)
        _status_leds.append(danger_led)
        danger_led.off()
        safe_led = LED(config.SAFE_LED_ID)
        _status_leds.append(safe_led)
        safe_led.off()
        return (danger_led, safe_led)
    except Exception as error:
        _force_status_leds_off()
        raise RuntimeError("onboard status LED initialization failed: " + str(error))


def _force_status_leds_off():
    for led in _status_leds:
        try:
            led.off()
        except Exception:
            pass


def _poll_control_uart(uart, receiver, state, now_ms):
    if uart is None:
        return

    try:
        while uart.any():
            available = uart.any()
            count = min(max(1, available), config.CONTROL_READ_CHUNK_BYTES)
            data = uart.read(count)
            if not data:
                break

            previous_overflow_count = receiver.overflow_count
            lines = receiver.feed(data)
            if receiver.overflow_count != previous_overflow_count:
                state.note_invalid()
                print("CTL_REJECT reason=overflow")

            for line in lines:
                if state.accept(line, now_ms, time.ticks_diff):
                    print(
                        "CTL_ACCEPT seq=%d danger=%d monitor=%d level=%d"
                        % (
                            state.last_sequence,
                            1 if state.danger else 0,
                            1 if state.person_enable else 0,
                            state.environmental_level,
                        )
                    )
                else:
                    print("CTL_REJECT reason=invalid-or-replay")
    except Exception as error:
        # A UART receive failure must not silently leave the camera in the
        # low-duty baseline. Latch fail-safe full-rate monitoring instead.
        state.note_invalid()
        print("CTL_REJECT reason=uart-read error=%s" % str(error))


def run():
    _init_sensor()
    detector = vision_detector.create_detector()
    uart = _init_uart()
    danger_led, safe_led = _init_status_leds()
    control_receiver = control.BoundedLineReceiver(
        config.CONTROL_MAX_LINE_BYTES
    )
    control_state = control.ControlState(config.CONTROL_TIMEOUT_MS)
    danger_blink = control.BlinkState(config.DANGER_LED_HALF_PERIOD_MS)
    warning_blink = control.BlinkState(config.WARNING_LED_HALF_PERIOD_MS)
    safe_blink = control.BlinkState(config.SAFE_LED_HALF_PERIOD_MS)

    print("VISION_READY board=%s mode=%s uart=%s ctl=%s" % (
        config.BOARD_MODEL,
        detector.mode_name,
        "on" if uart is not None else "off",
        config.CONTROL_CONTRACT_VERSION,
    ))

    sequence = 0
    last_send_ms = time.ticks_ms()
    last_fps_ms = time.ticks_ms()
    last_detection_ms = None
    clock = time.clock()
    frame_size_verified = False
    last_result = (0, 0, 0, 0, 0, None)
    last_alert_person = False
    last_control_mode = None
    last_status_led_mode = control.STATUS_OFF
    last_danger_led_output = False
    last_safe_led_output = False

    while True:
        now_ms = time.ticks_ms()
        _poll_control_uart(uart, control_receiver, control_state, now_ms)

        full_rate = control_state.full_rate_enabled(now_ms, time.ticks_diff)
        effective_alert = control_state.effective_alert_active(
            now_ms, time.ticks_diff
        )
        if effective_alert:
            control_mode = "alert"
        elif full_rate:
            control_mode = "failsafe"
        else:
            control_mode = "baseline"

        if control_mode != last_control_mode:
            print("VISION_CONTROL mode=%s" % control_mode)
            last_control_mode = control_mode

        detection_due = (
            last_detection_ms is None
            or full_rate
            or time.ticks_diff(now_ms, last_detection_ms)
            >= config.BASELINE_DETECTION_INTERVAL_MS
        )
        if detection_due:
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

            last_result = detector.detect(frame, alert_mode=effective_alert)
            last_alert_person = detector.alert_person_present()
            last_detection_ms = now_ms

            if config.DEBUG_DRAW:
                vision_detector.draw_debug(frame, last_result)

        now_ms = time.ticks_ms()
        status_led_mode = control.status_led_mode(
            control_state, last_alert_person, now_ms, time.ticks_diff
        )
        danger_phase_on = danger_blink.update(
            status_led_mode == control.STATUS_ALERT_RED,
            now_ms,
            time.ticks_diff,
        )
        warning_phase_on = warning_blink.update(
            status_led_mode == control.STATUS_ALERT_YELLOW,
            now_ms,
            time.ticks_diff,
        )
        safe_phase_on = safe_blink.update(
            status_led_mode == control.STATUS_SAFE_GREEN,
            now_ms,
            time.ticks_diff,
        )

        # Yellow is an explicit third state: its one phase drives red and green
        # together. It is never produced by two independent gates coinciding.
        status_phase_on = (
            danger_phase_on or warning_phase_on or safe_phase_on
        )
        danger_led_output, safe_led_output = control.status_led_channels(
            status_led_mode, status_phase_on
        )

        # Every status-mode transition passes through both LEDs OFF before the
        # new color is applied. Ordinary blink edges also apply OFF before ON.
        if status_led_mode != last_status_led_mode:
            danger_led.off()
            safe_led.off()
            last_danger_led_output = False
            last_safe_led_output = False
        if last_danger_led_output and not danger_led_output:
            danger_led.off()
        if last_safe_led_output and not safe_led_output:
            safe_led.off()
        if danger_led_output and not last_danger_led_output:
            danger_led.on()
        if safe_led_output and not last_safe_led_output:
            safe_led.on()
        last_danger_led_output = danger_led_output
        last_safe_led_output = safe_led_output
        last_status_led_mode = status_led_mode

        if time.ticks_diff(now_ms, last_send_ms) >= config.SEND_INTERVAL_MS:
            encoded = protocol.encode_vis(
                sequence,
                last_result[0],
                last_result[1],
                last_result[2],
                last_result[3],
                last_result[4],
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
            print(
                "VISION_SCORE raw=%d vis_person=%d alert_person=%d FPS=%.2f"
                % (
                    last_result[1],
                    last_result[0],
                    1 if last_alert_person else 0,
                    clock.fps(),
                )
            )
            last_fps_ms = now_ms

        if not detection_due:
            time.sleep_ms(5)


try:
    run()
except Exception as error:
    # Do not emit fake "no target" frames after a fatal camera/detector error.
    # The ESP32 treats a missing VIS heartbeat as a sensor fault. Never leave a
    # stale alert indication lit after a fatal error.
    _force_status_leds_off()
    print("VISION_FATAL:", error)
    raise
