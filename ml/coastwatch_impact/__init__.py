"""CoastWatch ImpactNet v2 research package.

The package is isolated from the legacy :mod:`coastal_risk` demonstrator. All
deployable services remain in Shadow Mode and label semantics are explicit.
"""

from .config import ImpactConfig, load_config, model_name_for_label_mode

__all__ = ["ImpactConfig", "load_config", "model_name_for_label_mode"]
__version__ = "0.1.0"
