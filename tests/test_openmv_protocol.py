import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "firmware" / "openmv"))

import protocol


class OpenMVProtocolTests(unittest.TestCase):
    def test_readme_vis_vector(self):
        frame = protocol.encode_vis(17, True, 90, 0, 0, True)
        self.assertEqual(frame, "$VIS,17,1,90,0,0,1*43\r\n")
        self.assertTrue(protocol.verify_frame(frame))
        self.assertEqual(
            protocol.decode_vis(frame),
            (17, 1, 90, 0, 0, 1),
        )

    def test_no_target_is_canonical(self):
        frame = protocol.encode_vis(18, False, 99, 120, 80, True)
        self.assertEqual(frame, "$VIS,18,0,0,0,0,0*75\r\n")
        self.assertEqual(protocol.decode_vis(frame), (18, 0, 0, 0, 0, 0))

    def test_fields_are_clamped(self):
        frame = protocol.encode_vis(999999, True, 800, -10, 9000, True)
        self.assertEqual(
            protocol.decode_vis(frame),
            (65535, 1, 100, 0, 4095, 1),
        )

    def test_bad_checksum_is_rejected(self):
        self.assertFalse(
            protocol.verify_frame("$VIS,17,1,83,128,96,1*5A\r\n")
        )
        with self.assertRaises(ValueError):
            protocol.decode_vis("$VIS,17,1,83,128,96,1*5A\r\n")

    def test_sequence_wraps(self):
        self.assertEqual(protocol.next_sequence(65534), 65535)
        self.assertEqual(protocol.next_sequence(65535), 0)


if __name__ == "__main__":
    unittest.main()
