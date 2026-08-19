"""OpenMV vision configuration.

This file intentionally keeps all board-specific and scene-specific values in
one place. The checked-in defaults target the confirmed H7 Plus full-duplex
ESP32 bench wiring; debug frames remain visible on the USB console.
"""

MODE_FACE_DETECTION = "face_detection"
MODE_COLOR_MARKER_DEMO = "color_marker_demo"
MODE_PERSON_CLASSIFIER = "person_classifier"

BOARD_MODEL = "MV4_H7_PLUS"
CONTROL_CONTRACT_VERSION = "1.1"

# Current MVP: use the firmware's built-in whole-frame person classifier.
# The complete camera view is the warning region, so stable person presence
# means both target_detected=1 and in_zone=1. This does not identify who the
# person is and does not provide a body bounding box.
VISION_MODE = MODE_PERSON_CLASSIFIER

# The built-in person model example uses QVGA RGB input.
FRAME_SIZE_NAME = "QVGA"
FRAME_WIDTH = 320
FRAME_HEIGHT = 240
VERTICAL_FLIP = False
HORIZONTAL_MIRROR = True
CAMERA_WARMUP_MS = 2000

# Legacy geometric ROIs retained for the optional face/color modes. The
# person-classifier mode ignores these because its complete frame is the
# warning region.
MONITOR_ROI = (0, 0, 240, 160)
DANGER_ROI = (0, 80, 240, 80)

# Require consecutive frames before changing state to avoid boundary flicker.
TARGET_ENTER_FRAMES = 3
TARGET_EXIT_FRAMES = 3
ZONE_ENTER_FRAMES = 3
ZONE_EXIT_FRAMES = 5

# Firmware-bundled two-class person/no-person classifier. Bench calibration:
# stable person samples were generally 0.75..0.96; stable empty samples were
# generally 0.13..0.46. The gap between thresholds provides hysteresis.
PERSON_MODEL_PATH = "/rom/person_detect.tflite"
PERSON_CLASS_INDEX = 1
PERSON_ENTER_THRESHOLD = 0.65
PERSON_EXIT_THRESHOLD = 0.58

# The VIS/telemetry decision above stays deliberately conservative because it
# feeds the ESP32 local alarm.  The status LED has a separate, alert-only
# high-recall decision: on the connected bench, a real person scored
# 0.5508..0.6445 while the earlier empty-scene calibration topped out near
# 0.46.  Two hits in a three-frame window tolerate one dim/blurred frame without
# letting the more sensitive indication leak into VIS or model-training data.
ALERT_PERSON_ENTER_THRESHOLD = 0.54
ALERT_PERSON_EXIT_THRESHOLD = 0.50
ALERT_PERSON_WINDOW_FRAMES = 3
ALERT_PERSON_REQUIRED_HITS = 2
ALERT_PERSON_EXIT_FRAMES = 5

# Built-in Haar face detector. The two names cover the local 4.7/4.8 examples.
FACE_CASCADE_PATHS = (
    "/rom/haarcascade_frontalface.cascade",
    "frontalface",
)
FACE_CASCADE_STAGES = 25
FACE_THRESHOLD = 0.75
FACE_SCALE_FACTOR = 1.25
FACE_MIN_AREA_PIXELS = 400

# Optional color-marker demo. Use the IDE threshold editor to replace this LAB
# tuple for the actual marker and lighting.
COLOR_THRESHOLD = (15, 43, 25, 60, -32, 127)
COLOR_PIXELS_THRESHOLD = 200
COLOR_AREA_THRESHOLD = 200
COLOR_LOCK_SETTLE_MS = 500

# The ESP32 sends CTL at 2 Hz. Three seconds without a newly sequenced, valid
# command is a control-link fault. Boot/fault/fail-safe and effective alert run
# the classifier as fast as the board can sustain; a fresh command with
# person_enable=0 reduces inference load to one real classification every
# 500 ms. VIS remains a 10 Hz heartbeat and reuses the most recent genuine
# result between baseline
# classifications (it never invents a person=0 sample).
SEND_INTERVAL_MS = 100
BASELINE_DETECTION_INTERVAL_MS = 500
CONTROL_TIMEOUT_MS = 3000
CONTROL_MAX_LINE_BYTES = 64
CONTROL_READ_CHUNK_BYTES = 64

# Board status is one explicit mode: green safe, yellow alert-without-person
# (red+green driven from one shared phase), red alert-with-person, or off.
# Advisory, fail-safe, local fault 4, and unknown states keep both channels off.
DANGER_LED_ID = 1
DANGER_LED_HALF_PERIOD_MS = 200
WARNING_LED_HALF_PERIOD_MS = 400
SAFE_LED_ID = 2
SAFE_LED_HALF_PERIOD_MS = 800

DEBUG_DRAW = True
DEBUG_PRINT_FRAMES = True
DEBUG_PRINT_FPS_EVERY_MS = 2000

# MV4/OpenMV H7 Plus standard mapping: UART3 TX=P4, RX=P5.
# Full duplex single-board integration: P4 sends VIS to ESP32 GPIO8/RX and P5
# receives CTL from ESP32 GPIO14/TX.
UART_ENABLED = True
UART_ID = 3
UART_TX_PIN = "P4"
UART_RX_PIN = "P5"
UART_BAUD = 115200
UART_TIMEOUT_CHAR_MS = 20

# Protocol limits.
SEQUENCE_MAX = 65535
COORDINATE_MAX = 4095
