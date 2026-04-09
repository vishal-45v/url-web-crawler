from app.services.classifier.base import BaseClassifier
from app.services.classifier.keybert_classifier import KeyBERTClassifier
from app.services.classifier.ollama_classifier import OllamaClassifier
from app.config.settings import settings

# Singleton registry — each classifier type is instantiated once per process lifetime
_registry: dict[str, BaseClassifier] = {}


def get_classifier() -> BaseClassifier:
    """
    Factory + singleton for classifiers.
    Controlled entirely by the CLASSIFIER env var — no code changes needed to switch.
    """
    key = settings.classifier

    if key not in _registry:
        if key == "keybert":
            _registry[key] = KeyBERTClassifier()
        elif key == "ollama":
            _registry[key] = OllamaClassifier()
        else:
            raise ValueError(
                f"Unknown classifier: '{key}'. Valid options: keybert, ollama"
            )

    return _registry[key]
