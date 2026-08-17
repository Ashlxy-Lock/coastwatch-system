"""OpenMV vision configuration.

This file intentionally keeps all board-specific and scene-specific values in
one place. The defaults are safe for an IDE bench test: frames are printed to
the USB debug console and the hardware UART is disabled until the exact board
model and pin mapping are confirmed.
"""

MODE_FACE_DETECTION = "face_detection"
MODE_COLOR_MARKER_DEMO = "color_marker_demo"
MODE_PERSON_CLASSIFIER = "person_classifier"

BOARD_MODEL = "MV4_H7_PLUS"

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

# VIS output is throttled to 10 Hz. Keep the debug console enabled while only
# the OpenMV board is available.
SEND_INTERVAL_MS = 100
DEBUG_DRAW = True
DEBUG_PRINT_FRAMES = True
DEBUG_PRINT_FPS_EVERY_MS = 2000

# MV4/OpenMV H7 Plus standard mapping: UART3 TX=P4, RX=P5.
# Enabled for the STM32F103ZET6 USART3 integration test.
UART_ENABLED = True
UART_ID = 3
UART_TX_PIN = "P4"
UART_RX_PIN = "P5"
UART_BAUD = 115200
UART_TIMEOUT_CHAR_MS = 20

# Protocol limits.
SEQUENCE_MAX = 65535
COORDINATE_MAX = 4095
