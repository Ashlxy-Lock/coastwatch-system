"""Bounded UART control reception and state for the OpenMV node.

This module deliberately has no camera or ``pyb`` imports so its framing,
sequence, timeout, and blink scheduling can also be tested on CPython.
"""

import protocol


STATUS_OFF = 0
STATUS_SAFE_GREEN = 1
STATUS_ALERT_YELLOW = 2
STATUS_ALERT_RED = 3


def _default_ticks_diff(newer, older):
    return int(newer) - int(older)


def _sequence_is_newer(candidate, previous):
    delta = (int(candidate) - int(previous)) & 0xFFFF
    return delta != 0 and delta < 0x8000


class BoundedLineReceiver:
    """Collect CRLF protocol lines without allowing an unbounded buffer.

    Once a line exceeds the configured limit, bytes are discarded through the
    next LF. This provides deterministic recovery at the following frame.
    """

    def __init__(self, maximum_line_bytes):
        if int(maximum_line_bytes) < 8:
            raise ValueError("maximum_line_bytes is too small")
        self.maximum_line_bytes = int(maximum_line_bytes)
        self._buffer = bytearray()
        self._discarding = False
        self.overflow_count = 0

    def feed(self, data):
        completed = []
        if data is None:
            return completed
        if isinstance(data, str):
            data = data.encode("ascii")

        for value in data:
            # MicroPython and CPython both yield integers when iterating bytes,
            # but accept a one-character value for defensive portability.
            if not isinstance(value, int):
                value = ord(value)

            if self._discarding:
                if value == 10:
                    self._discarding = False
                continue

            self._buffer.append(value)
            if len(self._buffer) > self.maximum_line_bytes:
                self._buffer = bytearray()
                self._discarding = True
                self.overflow_count += 1
                continue

            if value == 10:
                completed.append(bytes(self._buffer))
                self._buffer = bytearray()

        return completed


class ControlState:
    """Last accepted, monotonically sequenced ESP32 control command."""

    def __init__(self, timeout_ms):
        if int(timeout_ms) <= 0:
            raise ValueError("timeout_ms must be positive")
        self.timeout_ms = int(timeout_ms)
        self.last_sequence = None
        self.last_update_ms = None
        self.danger = False
        self.person_enable = False
        self.environmental_level = 0
        self.rejected_frames = 0
        self.invalid_latched = False

    def is_fresh(self, now_ms, ticks_diff=None):
        if self.last_update_ms is None:
            return False
        if ticks_diff is None:
            ticks_diff = _default_ticks_diff
        age = ticks_diff(now_ms, self.last_update_ms)
        return age >= 0 and age <= self.timeout_ms

    def accept(self, frame, now_ms, ticks_diff=None):
        try:
            seq, danger, person_enable, environmental_level = (
                protocol.decode_control(frame)
            )
        except Exception:
            self.rejected_frames += 1
            self.invalid_latched = True
            return False

        fresh = self.is_fresh(now_ms, ticks_diff)
        if (
            self.last_sequence is not None
            and fresh
            and not _sequence_is_newer(seq, self.last_sequence)
        ):
            # Duplicates and replayed commands do not refresh the timeout.
            self.rejected_frames += 1
            self.invalid_latched = True
            return False

        self.last_sequence = seq
        self.last_update_ms = now_ms
        self.danger = bool(danger)
        self.person_enable = bool(person_enable)
        self.environmental_level = environmental_level
        self.invalid_latched = False
        return True

    def note_invalid(self):
        self.rejected_frames += 1
        self.invalid_latched = True

    def full_rate_enabled(self, now_ms, ticks_diff=None):
        # Boot, corrupt/replayed input, and timeout all fail safe to full-rate
        # monitoring. A fresh command may request the lower duty baseline by
        # clearing person_enable.
        if self.invalid_latched or not self.is_fresh(now_ms, ticks_diff):
            return True
        return self.person_enable

    def effective_alert_active(self, now_ms, ticks_diff=None):
        return (
            self.is_fresh(now_ms, ticks_diff)
            and not self.invalid_latched
            and self.danger
            and self.person_enable
        )

    def trusted_danger_active(self, now_ms, ticks_diff=None):
        # Compatibility alias for the original model-only name. CTL v1.1
        # broadens this state to model OR healthy local water alert.
        return self.effective_alert_active(now_ms, ticks_diff)

    def trusted_safe_active(self, now_ms, ticks_diff=None):
        # Under the CTL contract, a trusted model result at level 0 is encoded
        # as danger=0/person_enable=0. Fail-safe and fallback commands use
        # person_enable=1, so they can never impersonate the green safe state.
        return (
            self.is_fresh(now_ms, ticks_diff)
            and not self.invalid_latched
            and not self.danger
            and not self.person_enable
            and self.environmental_level == 0
        )


class BlinkState:
    """Non-blocking square-wave scheduler for the red onboard LED."""

    def __init__(self, half_period_ms):
        if int(half_period_ms) <= 0:
            raise ValueError("half_period_ms must be positive")
        self.half_period_ms = int(half_period_ms)
        self.output_on = False
        self._last_change_ms = None
        self._enabled = False

    def update(self, enabled, now_ms, ticks_diff=None):
        if ticks_diff is None:
            ticks_diff = _default_ticks_diff

        if not enabled:
            self.output_on = False
            self._last_change_ms = now_ms
            self._enabled = False
            return False

        if not self._enabled:
            self.output_on = True
            self._last_change_ms = now_ms
            self._enabled = True
            return True

        if ticks_diff(now_ms, self._last_change_ms) >= self.half_period_ms:
            self.output_on = not self.output_on
            self._last_change_ms = now_ms
        return self.output_on


def alarm_led_gate(state, stable_person_detected, now_ms, ticks_diff=None):
    """True only for a fresh effective alert plus stable person result."""
    return bool(stable_person_detected) and state.effective_alert_active(
        now_ms, ticks_diff
    )


def safe_led_gate(state, now_ms, ticks_diff=None):
    """True only for a fresh, valid, trusted environmental level 0."""
    return state.trusted_safe_active(now_ms, ticks_diff)


def status_led_mode(
    state, stable_person_detected, now_ms, ticks_diff=None
):
    """Return one explicit, mutually exclusive board status indication."""
    if state.effective_alert_active(now_ms, ticks_diff):
        if stable_person_detected:
            return STATUS_ALERT_RED
        return STATUS_ALERT_YELLOW
    if state.trusted_safe_active(now_ms, ticks_diff):
        return STATUS_SAFE_GREEN
    return STATUS_OFF


def status_led_channels(mode, phase_on):
    """Map one explicit status mode/phase to ``(red_on, green_on)``."""
    if not phase_on:
        return (False, False)
    if mode == STATUS_SAFE_GREEN:
        return (False, True)
    if mode == STATUS_ALERT_YELLOW:
        return (True, True)
    if mode == STATUS_ALERT_RED:
        return (True, False)
    return (False, False)
