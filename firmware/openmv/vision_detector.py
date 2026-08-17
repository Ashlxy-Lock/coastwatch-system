"""Detection backends for the coastal warning OpenMV node."""

import image

import config


def _validate_roi(name, roi):
    if len(roi) != 4:
        raise ValueError(name + " must be (x, y, width, height)")
    x, y, width, height = roi
    if width <= 0 or height <= 0:
        raise ValueError(name + " width and height must be positive")
    if x < 0 or y < 0:
        raise ValueError(name + " origin must be inside the frame")
    if x + width > config.FRAME_WIDTH or y + height > config.FRAME_HEIGHT:
        raise ValueError(name + " exceeds the configured frame")


def _point_in_roi(x, y, roi):
    left, top, width, height = roi
    return (
        x >= left
        and y >= top
        and x < left + width
        and y < top + height
    )


def _roi_contains(outer, inner):
    outer_x, outer_y, outer_width, outer_height = outer
    inner_x, inner_y, inner_width, inner_height = inner
    return (
        inner_x >= outer_x
        and inner_y >= outer_y
        and inner_x + inner_width <= outer_x + outer_width
        and inner_y + inner_height <= outer_y + outer_height
    )


def _validate_rois():
    _validate_roi("MONITOR_ROI", config.MONITOR_ROI)
    _validate_roi("DANGER_ROI", config.DANGER_ROI)
    if not _roi_contains(config.MONITOR_ROI, config.DANGER_ROI):
        raise ValueError("DANGER_ROI must be fully inside MONITOR_ROI")


def _area_score(area, roi):
    """Map target area to a 50..99 quality score.

    Haar detection does not expose a probability. This deterministic heuristic
    exists only to keep the documented VIS field useful during the baseline.
    """
    roi_area = max(1, roi[2] * roi[3])
    return min(99, 50 + ((int(area) * 500) // roi_area))


class _StableTargetFilter:
    def __init__(self):
        self.present = False
        self.in_zone = False
        self.seen_count = 0
        self.missing_count = 0
        self.zone_seen_count = 0
        self.zone_clear_count = 0
        self.last_score = 0
        self.last_cx = 0
        self.last_cy = 0

    def update(self, raw_detected, raw_score, raw_cx, raw_cy, raw_zone):
        if raw_detected:
            self.seen_count += 1
            self.missing_count = 0
            self.last_score = raw_score
            self.last_cx = raw_cx
            self.last_cy = raw_cy

            if raw_zone:
                self.zone_seen_count += 1
                self.zone_clear_count = 0
            else:
                self.zone_seen_count = 0
                self.zone_clear_count += 1

            if not self.present and self.seen_count >= config.TARGET_ENTER_FRAMES:
                self.present = True
        else:
            self.seen_count = 0
            self.missing_count += 1
            self.zone_seen_count = 0
            self.zone_clear_count = 0
            if self.present and self.missing_count >= config.TARGET_EXIT_FRAMES:
                self.present = False
                self.in_zone = False

        if self.present and raw_detected:
            if (
                not self.in_zone
                and self.zone_seen_count >= config.ZONE_ENTER_FRAMES
            ):
                self.in_zone = True
            elif (
                self.in_zone
                and self.zone_clear_count >= config.ZONE_EXIT_FRAMES
            ):
                self.in_zone = False

        if not self.present:
            return (0, 0, 0, 0, 0)

        return (
            1,
            self.last_score,
            self.last_cx,
            self.last_cy,
            1 if self.in_zone else 0,
        )


class _ScoreHysteresis:
    def __init__(
        self,
        enter_threshold,
        exit_threshold,
        enter_frames,
        exit_frames,
    ):
        self.enter_threshold = enter_threshold
        self.exit_threshold = exit_threshold
        self.enter_frames = enter_frames
        self.exit_frames = exit_frames
        self.present = False
        self.enter_count = 0
        self.exit_count = 0

    def update(self, score):
        if not self.present:
            self.exit_count = 0
            if score >= self.enter_threshold:
                self.enter_count += 1
                if self.enter_count >= self.enter_frames:
                    self.present = True
                    self.enter_count = 0
            else:
                self.enter_count = 0
        else:
            self.enter_count = 0
            if score < self.exit_threshold:
                self.exit_count += 1
                if self.exit_count >= self.exit_frames:
                    self.present = False
                    self.exit_count = 0
            else:
                self.exit_count = 0

        return self.present


class PersonClassifierDetector:
    """Whole-frame person presence using the firmware-bundled TFLite model."""

    mode_name = config.MODE_PERSON_CLASSIFIER

    def __init__(self, model=None):
        if model is None:
            import ml

            model = ml.Model(config.PERSON_MODEL_PATH)

        self._model = model
        self._filter = _ScoreHysteresis(
            config.PERSON_ENTER_THRESHOLD,
            config.PERSON_EXIT_THRESHOLD,
            config.TARGET_ENTER_FRAMES,
            config.TARGET_EXIT_FRAMES,
        )

        labels = self._model.labels
        if labels is not None and config.PERSON_CLASS_INDEX >= len(labels):
            raise RuntimeError("person class index exceeds model labels")

    def detect(self, frame):
        scores = self._model.predict([frame])[0].flatten().tolist()
        if config.PERSON_CLASS_INDEX >= len(scores):
            raise RuntimeError("person class index exceeds model output")

        raw_score = float(scores[config.PERSON_CLASS_INDEX])
        present = self._filter.update(raw_score)
        if not present:
            return (0, 0, 0, 0, 0, None)

        score = max(0, min(100, int(round(raw_score * 100))))

        # This is a whole-frame classifier, so a body coordinate is not
        # available. The full camera view is the warning region by definition.
        return (1, score, 0, 0, 1, None)


class FaceDetector:
    mode_name = config.MODE_FACE_DETECTION

    def __init__(self):
        _validate_rois()
        self._cascade = self._load_cascade()
        self._filter = _StableTargetFilter()

    @staticmethod
    def _load_cascade():
        last_error = None
        for path in config.FACE_CASCADE_PATHS:
            try:
                return image.HaarCascade(
                    path,
                    stages=config.FACE_CASCADE_STAGES,
                )
            except Exception as error:
                last_error = error
        raise RuntimeError("unable to load frontal-face cascade: " + str(last_error))

    def detect(self, frame):
        objects = frame.find_features(
            self._cascade,
            config.FACE_THRESHOLD,
            config.FACE_SCALE_FACTOR,
            config.MONITOR_ROI,
        )

        largest = None
        largest_area = 0
        largest_danger = None
        largest_danger_area = 0
        for rect in objects:
            area = rect[2] * rect[3]
            if area < config.FACE_MIN_AREA_PIXELS:
                continue

            if area > largest_area:
                largest = rect
                largest_area = area

            rect_cx = rect[0] + (rect[2] // 2)
            rect_cy = rect[1] + (rect[3] // 2)
            if (
                _point_in_roi(rect_cx, rect_cy, config.DANGER_ROI)
                and area > largest_danger_area
            ):
                largest_danger = rect
                largest_danger_area = area

        selected = largest_danger if largest_danger is not None else largest
        if selected is None:
            stable = self._filter.update(False, 0, 0, 0, False)
            return stable + (None,)

        x, y, width, height = selected
        selected_area = width * height
        cx = x + (width // 2)
        cy = y + (height // 2)
        raw_zone = _point_in_roi(cx, cy, config.DANGER_ROI)
        score = _area_score(selected_area, config.MONITOR_ROI)
        stable = self._filter.update(True, score, cx, cy, raw_zone)
        return stable + (selected,)


class ColorMarkerDetector:
    mode_name = config.MODE_COLOR_MARKER_DEMO

    def __init__(self):
        _validate_rois()
        self._filter = _StableTargetFilter()

    def detect(self, frame):
        blobs = frame.find_blobs(
            [config.COLOR_THRESHOLD],
            roi=config.MONITOR_ROI,
            pixels_threshold=config.COLOR_PIXELS_THRESHOLD,
            area_threshold=config.COLOR_AREA_THRESHOLD,
            merge=True,
        )

        largest = None
        largest_pixels = 0
        largest_danger = None
        largest_danger_pixels = 0
        for blob in blobs:
            pixels = blob.pixels()
            if pixels > largest_pixels:
                largest = blob
                largest_pixels = pixels

            if (
                _point_in_roi(blob.cx(), blob.cy(), config.DANGER_ROI)
                and pixels > largest_danger_pixels
            ):
                largest_danger = blob
                largest_danger_pixels = pixels

        selected = largest_danger if largest_danger is not None else largest
        if selected is None:
            stable = self._filter.update(False, 0, 0, 0, False)
            return stable + (None,)

        selected_pixels = selected.pixels()
        cx = selected.cx()
        cy = selected.cy()
        raw_zone = _point_in_roi(cx, cy, config.DANGER_ROI)
        score = _area_score(selected_pixels, config.MONITOR_ROI)
        stable = self._filter.update(True, score, cx, cy, raw_zone)
        return stable + (selected.rect(),)


def create_detector():
    if config.VISION_MODE == config.MODE_PERSON_CLASSIFIER:
        return PersonClassifierDetector()
    if config.VISION_MODE == config.MODE_FACE_DETECTION:
        return FaceDetector()
    if config.VISION_MODE == config.MODE_COLOR_MARKER_DEMO:
        return ColorMarkerDetector()
    raise ValueError("unsupported VISION_MODE: " + str(config.VISION_MODE))


def draw_debug(frame, result):
    detected, score, cx, cy, in_zone, raw_rect = result

    if config.VISION_MODE == config.MODE_PERSON_CLASSIFIER:
        status_color = (0, 255, 0) if detected else (255, 0, 0)
        frame.draw_rectangle(
            (0, 0, config.FRAME_WIDTH - 1, config.FRAME_HEIGHT - 1),
            color=status_color,
            thickness=2,
        )
        frame.draw_string(
            2,
            2,
            "PERSON:%d SCORE:%d" % (detected, score),
            color=status_color,
        )
        return

    grayscale = config.VISION_MODE == config.MODE_FACE_DETECTION
    monitor_color = 100 if grayscale else (0, 255, 0)
    danger_color = 180 if grayscale else (255, 165, 0)
    target_color = 255 if grayscale else (255, 0, 0)

    frame.draw_rectangle(config.MONITOR_ROI, color=monitor_color)
    frame.draw_rectangle(config.DANGER_ROI, color=danger_color)
    if raw_rect is not None:
        frame.draw_rectangle(raw_rect, color=target_color)
    if detected:
        frame.draw_cross(cx, cy, color=target_color)
        frame.draw_string(
            2,
            2,
            "S:%d Z:%d" % (score, in_zone),
            color=target_color,
        )
