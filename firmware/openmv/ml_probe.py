"""Read-only machine-learning capability probe for OpenMV4P-H7.

Open this file in OpenMV IDE and press Run. It does not load a model, write to
storage, or update firmware.
"""

import gc
import uos


gc.collect()

print("ML_PROBE_SYSTEM:", uos.uname())
print("ML_PROBE_MEM_FREE:", gc.mem_free())

try:
    rom_models = [
        name for name in uos.listdir("/rom") if name.lower().endswith(".tflite")
    ]
    print("ML_PROBE_ROM_MODELS:", rom_models)
except Exception as error:
    print("ML_PROBE_ROM_MODELS: unavailable", repr(error))

try:
    import ml

    print("ML_PROBE_MODEL_API:", hasattr(ml, "Model"))

    try:
        from ml.postprocessing import edgeimpulse

        print("ML_PROBE_FOMO_API:", hasattr(edgeimpulse, "Fomo"))
    except Exception as error:
        print("ML_PROBE_FOMO_API: False", repr(error))
except Exception as error:
    print("ML_PROBE_MODEL_API: False", repr(error))
