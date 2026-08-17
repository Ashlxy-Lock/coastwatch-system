import importlib
import sys
import types
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OPENMV_DIR = PROJECT_ROOT / "firmware" / "openmv"
sys.path.insert(0, str(OPENMV_DIR))


fake_image = types.ModuleType("image")
fake_image.HaarCascade = lambda path, stages=25: (path, stages)
sys.modules["image"] = fake_image

config = importlib.import_module("config")
vision_detector = importlib.import_module("vision_detector")


class FakeFaceFrame:
    def __init__(self, rectangles):
        self.rectangles = rectangles

    def find_features(self, *args, **kwargs):
        return list(self.rectangles)


class FakeTensor:
    def __init__(self, values):
        self.values = values

    def flatten(self):
        return self

    def tolist(self):
        return list(self.values)


class FakePersonModel:
    labels = ("no_person", "person")

    def __init__(self, person_scores):
        self.person_scores = iter(person_scores)

    def predict(self, inputs):
        score = next(self.person_scores)
        return [FakeTensor((1.0 - score, score))]


class OpenMVVisionLogicTests(unittest.TestCase):
    def test_roi_uses_exclusive_right_and_bottom_edges(self):
        roi = (10, 20, 30, 40)
        self.assertTrue(vision_detector._point_in_roi(10, 20, roi))
        self.assertTrue(vision_detector._point_in_roi(39, 59, roi))
        self.assertFalse(vision_detector._point_in_roi(40, 59, roi))
        self.assertFalse(vision_detector._point_in_roi(39, 60, roi))

    def test_danger_roi_must_fit_inside_monitor_roi(self):
        self.assertTrue(
            vision_detector._roi_contains((0, 0, 100, 100), (20, 20, 50, 50))
        )
        self.assertFalse(
            vision_detector._roi_contains((0, 0, 100, 100), (90, 20, 20, 20))
        )

    def test_face_detection_is_confirmed_and_debounced(self):
        detector = vision_detector.FaceDetector()
        face_in_danger_zone = FakeFaceFrame([(20, 90, 40, 40)])

        self.assertEqual(detector.detect(face_in_danger_zone)[0], 0)
        self.assertEqual(detector.detect(face_in_danger_zone)[0], 0)
        confirmed = detector.detect(face_in_danger_zone)

        self.assertEqual(confirmed[0], 1)
        self.assertEqual(confirmed[2:4], (40, 110))
        self.assertEqual(confirmed[4], 1)

        no_face = FakeFaceFrame([])
        for _ in range(config.TARGET_EXIT_FRAMES - 1):
            self.assertEqual(detector.detect(no_face)[0], 1)
        self.assertEqual(detector.detect(no_face)[0:5], (0, 0, 0, 0, 0))

    def test_zone_confirmation_does_not_cross_a_missing_frame(self):
        stable_filter = vision_detector._StableTargetFilter()

        # Establish presence outside the zone.
        for _ in range(config.TARGET_ENTER_FRAMES):
            result = stable_filter.update(True, 70, 10, 10, False)
        self.assertEqual(result[0], 1)
        self.assertEqual(result[4], 0)

        # Two danger frames are not enough, and a miss resets the run.
        stable_filter.update(True, 70, 10, 100, True)
        stable_filter.update(True, 70, 10, 100, True)
        stable_filter.update(False, 0, 0, 0, False)
        result = stable_filter.update(True, 70, 10, 100, True)
        self.assertEqual(result[4], 0)

        stable_filter.update(True, 70, 10, 100, True)
        result = stable_filter.update(True, 70, 10, 100, True)
        self.assertEqual(result[4], 1)

    def test_largest_face_is_selected(self):
        detector = vision_detector.FaceDetector()
        frame = FakeFaceFrame(
            [
                (5, 5, 20, 20),
                (100, 30, 60, 50),
                (20, 40, 30, 30),
            ]
        )

        detector.detect(frame)
        detector.detect(frame)
        result = detector.detect(frame)
        self.assertEqual(result[2:4], (130, 55))
        self.assertEqual(result[5], (100, 30, 60, 50))

    def test_dangerous_face_is_selected_over_larger_safe_face(self):
        detector = vision_detector.FaceDetector()
        frame = FakeFaceFrame(
            [
                (20, 10, 90, 60),
                (150, 100, 30, 30),
            ]
        )

        detector.detect(frame)
        detector.detect(frame)
        result = detector.detect(frame)
        self.assertEqual(result[2:4], (165, 115))
        self.assertEqual(result[4], 1)
        self.assertEqual(result[5], (150, 100, 30, 30))

    def test_person_classifier_uses_full_frame_as_zone_with_hysteresis(self):
        hold_score = (
            config.PERSON_ENTER_THRESHOLD + config.PERSON_EXIT_THRESHOLD
        ) / 2.0
        scores = (
            [0.90] * config.TARGET_ENTER_FRAMES
            + [hold_score]
            + [0.20] * config.TARGET_EXIT_FRAMES
        )
        detector = vision_detector.PersonClassifierDetector(
            FakePersonModel(scores)
        )
        frame = object()

        for _ in range(config.TARGET_ENTER_FRAMES - 1):
            self.assertEqual(detector.detect(frame), (0, 0, 0, 0, 0, None))

        confirmed = detector.detect(frame)
        self.assertEqual(confirmed, (1, 90, 0, 0, 1, None))

        # A score in the hysteresis band holds the current state.
        expected_hold_score = int(round(hold_score * 100))
        self.assertEqual(
            detector.detect(frame)[0:5],
            (1, expected_hold_score, 0, 0, 1),
        )

        for _ in range(config.TARGET_EXIT_FRAMES - 1):
            self.assertEqual(detector.detect(frame)[0], 1)
        self.assertEqual(detector.detect(frame), (0, 0, 0, 0, 0, None))


if __name__ == "__main__":
    unittest.main()
