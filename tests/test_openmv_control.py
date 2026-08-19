import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "firmware" / "openmv"))

import control
import protocol


class ControlProtocolTests(unittest.TestCase):
    def test_cross_language_control_vectors(self):
        self.assertEqual(
            protocol.encode_control(17, True, True, 2),
            "$CTL,17,1,1,2*6F\r\n",
        )
        self.assertEqual(
            protocol.encode_control(18, False, False, 1),
            "$CTL,18,0,0,1*63\r\n",
        )
        self.assertEqual(
            protocol.encode_control(19, False, True, 2),
            "$CTL,19,0,1,2*60\r\n",
        )
        self.assertEqual(
            protocol.encode_control(18, False, False, 0),
            "$CTL,18,0,0,0*62\r\n",
        )
        self.assertEqual(
            protocol.encode_control(19, False, True, 1),
            "$CTL,19,0,1,1*63\r\n",
        )
        self.assertEqual(
            protocol.encode_control(20, True, True, 0),
            "$CTL,20,1,1,0*69\r\n",
        )

    def test_safe_danger_and_failsafe_frames_round_trip(self):
        cases = (
            (7, False, False, 0),
            (8, False, False, 1),
            (9, False, True, 0),
            (10, True, True, 2),
            (11, True, True, 3),
            (12, True, True, 0),
            (13, True, True, 1),
        )
        for values in cases:
            frame = protocol.encode_control(*values)
            self.assertTrue(frame.endswith("\r\n"))
            self.assertEqual(protocol.decode_control(frame), tuple(int(v) for v in values))

    def test_danger_requires_monitor_and_warning_or_higher(self):
        with self.assertRaises(ValueError):
            protocol.encode_control(1, True, False, 2)
        with self.assertRaises(ValueError):
            protocol.encode_control(1, False, False, 2)
        self.assertEqual(
            protocol.decode_control(
                protocol.encode_control(1, True, True, 1)
            ),
            (1, 1, 1, 1),
        )

    def test_control_decoder_rejects_noncanonical_spellings(self):
        canonical = protocol.encode_control(17, True, True, 2)
        payload = canonical[1 : canonical.rfind("*")]
        checksum = "%02X" % protocol.xor_checksum(payload)

        invalid_frames = (
            canonical[:-2] + "\n",
            canonical[:-4] + canonical[-4:-2].lower() + "\r\n",
            "$CTL,017,1,1,2*%02X\r\n"
            % protocol.xor_checksum("CTL,017,1,1,2"),
            "$CTL,17,1,1,2 *%02X\r\n"
            % protocol.xor_checksum("CTL,17,1,1,2 "),
            "$CTL,17,1,1,2,0*%02X\r\n"
            % protocol.xor_checksum("CTL,17,1,1,2,0"),
            "$CTL,17,1,0,2*%02X\r\n"
            % protocol.xor_checksum("CTL,17,1,0,2"),
            "$CTL,17,0,0,2*%02X\r\n"
            % protocol.xor_checksum("CTL,17,0,0,2"),
        )
        self.assertEqual(len(checksum), 2)
        for frame in invalid_frames:
            with self.assertRaises(ValueError, msg=repr(frame)):
                protocol.decode_control(frame)


class ControlStateTests(unittest.TestCase):
    def test_boot_timeout_and_invalid_input_fail_safe_to_full_rate(self):
        state = control.ControlState(3000)
        self.assertTrue(state.full_rate_enabled(0))
        self.assertFalse(state.trusted_danger_active(0))

        self.assertTrue(
            state.accept(protocol.encode_control(1, False, False, 0), 100)
        )
        self.assertFalse(state.full_rate_enabled(200))

        self.assertTrue(state.full_rate_enabled(3101))
        self.assertFalse(state.trusted_danger_active(3101))

        self.assertFalse(state.accept(b"garbage\r\n", 3200))
        self.assertTrue(state.full_rate_enabled(3200))
        self.assertFalse(state.trusted_danger_active(3200))

    def test_fresh_valid_frame_recovers_invalid_latch(self):
        state = control.ControlState(3000)
        self.assertTrue(
            state.accept(protocol.encode_control(1, False, False, 0), 10)
        )
        self.assertFalse(state.accept(b"bad\r\n", 20))
        self.assertTrue(state.invalid_latched)
        self.assertTrue(
            state.accept(protocol.encode_control(2, False, False, 1), 30)
        )
        self.assertFalse(state.invalid_latched)
        self.assertFalse(state.full_rate_enabled(30))

    def test_replay_does_not_refresh_timeout_and_sequence_wrap_is_valid(self):
        state = control.ControlState(3000)
        self.assertTrue(
            state.accept(protocol.encode_control(65535, True, True, 3), 10)
        )
        self.assertFalse(
            state.accept(protocol.encode_control(65535, True, True, 3), 20)
        )
        self.assertEqual(state.last_update_ms, 10)
        self.assertTrue(
            state.accept(protocol.encode_control(0, True, True, 3), 30)
        )
        self.assertTrue(state.trusted_danger_active(30))

    def test_failsafe_monitor_request_is_full_rate_but_not_trusted_danger(self):
        state = control.ControlState(3000)
        self.assertTrue(
            state.accept(protocol.encode_control(9, False, True, 2), 100)
        )
        self.assertTrue(state.full_rate_enabled(100))
        self.assertFalse(state.trusted_danger_active(100))

    def test_local_effective_alert_does_not_require_model_warning(self):
        state = control.ControlState(3000)
        self.assertTrue(
            state.accept(protocol.encode_control(20, True, True, 0), 100)
        )
        self.assertTrue(state.full_rate_enabled(100))
        self.assertTrue(state.effective_alert_active(100))
        self.assertFalse(control.safe_led_gate(state, 100))

    def test_green_safe_gate_requires_exact_fresh_trusted_level_zero(self):
        state = control.ControlState(3000)
        state.accept(protocol.encode_control(1, False, False, 0), 100)
        self.assertTrue(control.safe_led_gate(state, 100))

        state.accept(protocol.encode_control(2, False, False, 1), 200)
        self.assertFalse(control.safe_led_gate(state, 200))

        state.accept(protocol.encode_control(3, False, True, 0), 300)
        self.assertFalse(control.safe_led_gate(state, 300))

        state.accept(protocol.encode_control(4, False, False, 0), 400)
        self.assertFalse(control.safe_led_gate(state, 3401))

        state.accept(protocol.encode_control(5, False, False, 0), 4000)
        state.note_invalid()
        self.assertFalse(control.safe_led_gate(state, 4001))

    def test_red_and_green_gates_are_mutually_exclusive(self):
        frames = (
            protocol.encode_control(1, False, False, 0),
            protocol.encode_control(2, False, False, 1),
            protocol.encode_control(3, False, True, 2),
            protocol.encode_control(4, True, True, 2),
            protocol.encode_control(5, True, True, 3),
        )
        state = control.ControlState(3000)
        for index, frame in enumerate(frames):
            now_ms = index * 100
            self.assertTrue(state.accept(frame, now_ms))
            for person_detected in (False, True):
                red = control.alarm_led_gate(
                    state, person_detected, now_ms
                )
                green = control.safe_led_gate(state, now_ms)
                self.assertFalse(red and green)

    def test_explicit_status_modes_cover_safe_alert_and_off_states(self):
        state = control.ControlState(3000)

        state.accept(protocol.encode_control(1, False, False, 0), 0)
        self.assertEqual(
            control.status_led_mode(state, False, 0),
            control.STATUS_SAFE_GREEN,
        )

        # Local alarm 2/3 can assert effective danger while model level is 0.
        state.accept(protocol.encode_control(2, True, True, 0), 100)
        self.assertEqual(
            control.status_led_mode(state, False, 100),
            control.STATUS_ALERT_YELLOW,
        )
        self.assertEqual(
            control.status_led_mode(state, True, 100),
            control.STATUS_ALERT_RED,
        )

        # Local fault/unknown is encoded as monitor-on without danger.
        state.accept(protocol.encode_control(3, False, True, 0), 200)
        self.assertEqual(
            control.status_led_mode(state, True, 200),
            control.STATUS_OFF,
        )

        state.accept(protocol.encode_control(4, False, False, 1), 300)
        self.assertEqual(
            control.status_led_mode(state, False, 300),
            control.STATUS_OFF,
        )
        self.assertEqual(
            control.status_led_mode(state, False, 3301),
            control.STATUS_OFF,
        )

    def test_status_mode_maps_to_explicit_led_channels(self):
        self.assertEqual(
            control.status_led_channels(control.STATUS_OFF, True),
            (False, False),
        )
        self.assertEqual(
            control.status_led_channels(control.STATUS_SAFE_GREEN, True),
            (False, True),
        )
        self.assertEqual(
            control.status_led_channels(control.STATUS_ALERT_YELLOW, True),
            (True, True),
        )
        self.assertEqual(
            control.status_led_channels(control.STATUS_ALERT_RED, True),
            (True, False),
        )
        for mode in (
            control.STATUS_SAFE_GREEN,
            control.STATUS_ALERT_YELLOW,
            control.STATUS_ALERT_RED,
        ):
            self.assertEqual(
                control.status_led_channels(mode, False),
                (False, False),
            )


class ReceiverAndBlinkTests(unittest.TestCase):
    def test_overflow_discards_through_lf_then_resynchronizes(self):
        receiver = control.BoundedLineReceiver(32)
        self.assertEqual(receiver.feed(b"x" * 40), [])
        self.assertEqual(receiver.overflow_count, 1)
        self.assertEqual(receiver.feed(b"ignored\n"), [])

        valid = protocol.encode_control(1, False, False, 0).encode("ascii")
        self.assertEqual(receiver.feed(valid), [valid])

    def test_blink_state_turns_off_immediately_when_gate_clears(self):
        blink = control.BlinkState(200)
        self.assertTrue(blink.update(True, 0))
        self.assertTrue(blink.update(True, 199))
        self.assertFalse(blink.update(True, 200))
        self.assertFalse(blink.update(False, 201))
        self.assertTrue(blink.update(True, 202))

    def test_led_gate_rejects_failsafe_invalid_and_stale_person_results(self):
        state = control.ControlState(3000)
        state.accept(protocol.encode_control(1, False, True, 2), 0)
        self.assertFalse(control.alarm_led_gate(state, True, 1))

        state.accept(protocol.encode_control(2, True, True, 2), 10)
        self.assertFalse(control.alarm_led_gate(state, False, 10))
        self.assertTrue(control.alarm_led_gate(state, True, 10))
        self.assertFalse(control.alarm_led_gate(state, True, 3011))

        state.accept(protocol.encode_control(3, True, True, 3), 4000)
        state.note_invalid()
        self.assertFalse(control.alarm_led_gate(state, True, 4001))


if __name__ == "__main__":
    unittest.main()
