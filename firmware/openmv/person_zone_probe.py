"""Person-presence and danger-zone bench test for OpenMV4P-H7.

The built-in person model is an image classifier, not a bounding-box detector.
To estimate whether somebody is in the lower-half danger zone, this script
classifies three overlapping square tiles that cover that zone. The reported
tile center is approximate and must not be treated as a detected body position.

Open this file in OpenMV IDE and press Run. It does not write to storage or
update firmware.
"""

import ml
import sensor
import time

from ml.preprocessing import Normalization


MODEL_PATH = "/rom/person_detect.tflite"

# Thresholds calibrated from the first 96-frame bench capture:
# stable person samples were generally 0.75..0.96 and stable empty samples
# were generally 0.13..0.46. Hysteresis absorbs the ambiguous transition band.
PERSON_ENTER_THRESHOLD = 0.65
PERSON_EXIT_THRESHOLD = 0.50
ZONE_ENTER_THRESHOLD = 0.65
ZONE_EXIT_THRESHOLD = 0.50
PERSON_ENTER_SCANS = 3
PERSON_EXIT_SCANS = 5
ZONE_ENTER_SCANS = 2
ZONE_EXIT_SCANS = 3

FRAME_WIDTH = 320
FRAME_HEIGHT = 240
DANGER_ROI = (0, 120, 320, 120)
DANGER_TILES = (
    (0, 120, 120, 120),
    (100, 120, 120, 120),
    (200, 120, 120, 120),
)

REPORT_INTERVAL_MS = 500


class StableThreshold:
    def __init__(
        self,
        enter_threshold,
        exit_threshold,
        enter_scans,
        exit_scans,
    ):
        self.state = False
        self.enter_threshold = enter_threshold
        self.exit_threshold = exit_threshold
        self.enter_scans = enter_scans
        self.exit_scans = exit_scans
        self.above_count = 0
        self.below_count = 0

    def update(self, score):
        if score >= self.enter_threshold:
            self.above_count += 1
            self.below_count = 0
        elif score < self.exit_threshold:
            self.above_count = 0
            self.below_count += 1
        else:
            # Hold the previous state inside the hysteresis band.
            self.above_count = 0
            self.below_count = 0

        if not self.state and self.above_count >= self.enter_scans:
            self.state = True
        elif self.state and self.below_count >= self.exit_scans:
            self.state = False

        return self.state


def person_score(model, model_input):
    scores = model.predict([model_input])[0].flatten().tolist()
    if len(scores) < 2:
        raise RuntimeError("person model did not return two class scores")
    return scores[1]


sensor.reset()
sensor.set_pixformat(sensor.RGB565)
sensor.set_framesize(sensor.QVGA)
sensor.set_vflip(False)
sensor.set_hmirror(True)
sensor.skip_frames(time=2000)

model = ml.Model(MODEL_PATH)
tile_normalizers = tuple(Normalization(roi=roi) for roi in DANGER_TILES)
person_filter = StableThreshold(
    PERSON_ENTER_THRESHOLD,
    PERSON_EXIT_THRESHOLD,
    PERSON_ENTER_SCANS,
    PERSON_EXIT_SCANS,
)
zone_filter = StableThreshold(
    ZONE_ENTER_THRESHOLD,
    ZONE_EXIT_THRESHOLD,
    ZONE_ENTER_SCANS,
    ZONE_EXIT_SCANS,
)

print("ZONE_MODEL_LABELS:", model.labels)
print("ZONE_MODEL_INPUT:", model.input_shape, model.input_dtype)
print(
    "ZONE_MODEL_READY person=%.2f/%.2f zone=%.2f/%.2f tiles=%d"
    % (
        PERSON_ENTER_THRESHOLD,
        PERSON_EXIT_THRESHOLD,
        ZONE_ENTER_THRESHOLD,
        ZONE_EXIT_THRESHOLD,
        len(DANGER_TILES),
    )
)

clock = time.clock()
last_report_ms = time.ticks_ms()
locked_tile = 0

while True:
    clock.tick()
    frame = sensor.snapshot()

    full_score = person_score(model, frame)

    danger_score = 0.0
    best_tile = 0
    for index in range(len(tile_normalizers)):
        score = person_score(model, tile_normalizers[index](frame))
        if score > danger_score:
            danger_score = score
            best_tile = index

    if danger_score >= ZONE_ENTER_THRESHOLD:
        locked_tile = best_tile

    person_detected = person_filter.update(max(full_score, danger_score))
    in_zone = zone_filter.update(danger_score if person_detected else 0.0)
    displayed_tile = locked_tile if in_zone else best_tile
    zone_tile = displayed_tile if in_zone else -1

    frame.draw_rectangle(DANGER_ROI, color=(255, 165, 0), thickness=2)
    for index in range(len(DANGER_TILES)):
        color = (
            (0, 255, 0)
            if in_zone and index == displayed_tile
            else (120, 120, 120)
        )
        frame.draw_rectangle(DANGER_TILES[index], color=color)

    status_color = (0, 255, 0) if person_detected else (255, 0, 0)
    frame.draw_string(
        2,
        2,
        "P:%d %.2f Z:%d %.2f"
        % (
            1 if person_detected else 0,
            full_score,
            1 if in_zone else 0,
            danger_score,
        ),
        color=status_color,
    )

    now_ms = time.ticks_ms()
    if time.ticks_diff(now_ms, last_report_ms) >= REPORT_INTERVAL_MS:
        print(
            "ZONE_RESULT person=%d full_score=%.3f in_zone=%d "
            "danger_score=%.3f best_tile=%d zone_tile=%d fps=%.2f"
            % (
                1 if person_detected else 0,
                full_score,
                1 if in_zone else 0,
                danger_score,
                best_tile,
                zone_tile,
                clock.fps(),
            )
        )
        last_report_ms = now_ms
