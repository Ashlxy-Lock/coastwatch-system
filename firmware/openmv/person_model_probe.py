"""Bench test for the firmware's built-in person classifier.

Open this file in OpenMV IDE and press Run. The model is loaded directly from
ROM, so no model file needs to be copied to the camera.
"""

import ml
import sensor
import time


PERSON_THRESHOLD = 0.65
REPORT_INTERVAL_MS = 500


sensor.reset()
sensor.set_pixformat(sensor.RGB565)
sensor.set_framesize(sensor.QVGA)
sensor.set_vflip(False)
sensor.set_hmirror(False)
sensor.skip_frames(time=2000)

model = ml.Model("/rom/person_detect.tflite")

print("PERSON_MODEL_LABELS:", model.labels)
print("PERSON_MODEL_INPUT:", model.input_shape, model.input_dtype)
print("PERSON_MODEL_OUTPUT:", model.output_shape, model.output_dtype)
print("PERSON_MODEL_MEMORY: len=%d ram=%d" % (model.len, model.ram))
print("PERSON_MODEL_READY threshold=%.2f" % PERSON_THRESHOLD)

clock = time.clock()
last_report_ms = time.ticks_ms()

while True:
    clock.tick()
    frame = sensor.snapshot()
    scores = model.predict([frame])[0].flatten().tolist()

    best_index = 0
    for index in range(1, len(scores)):
        if scores[index] > scores[best_index]:
            best_index = index

    best_score = scores[best_index]
    best_label = (
        model.labels[best_index]
        if model.labels is not None and best_index < len(model.labels)
        else str(best_index)
    )

    # The built-in two-class model uses index 1 for "person".
    person_score = scores[1] if len(scores) > 1 else best_score
    person_detected = len(scores) > 1 and person_score >= PERSON_THRESHOLD

    border_color = (0, 255, 0) if person_detected else (255, 0, 0)
    frame.draw_rectangle(
        (0, 0, frame.width() - 1, frame.height() - 1),
        color=border_color,
        thickness=2,
    )
    frame.draw_string(
        2,
        2,
        "%s %.2f" % (best_label, best_score),
        color=border_color,
    )

    now_ms = time.ticks_ms()
    if time.ticks_diff(now_ms, last_report_ms) >= REPORT_INTERVAL_MS:
        print(
            "PERSON_RESULT detected=%d person_score=%.3f top=%s top_score=%.3f fps=%.2f"
            % (
                1 if person_detected else 0,
                person_score,
                best_label,
                best_score,
                clock.fps(),
            )
        )
        last_report_ms = now_ms

