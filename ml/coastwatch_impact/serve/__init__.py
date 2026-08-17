"""Shadow-only FastAPI inference service."""

from .app import create_app
from .model_loader import BundlePredictor, InsufficientDataError

__all__ = ["BundlePredictor", "InsufficientDataError", "create_app"]
