from abc import ABC, abstractmethod
from typing import List
from app.models.schemas import Topic


class BaseClassifier(ABC):
    """
    Strategy interface for topic classifiers.
    Swap implementations via CLASSIFIER env var — no code change needed.
    """

    @abstractmethod
    async def classify(self, text: str, title: str = "") -> List[Topic]:
        """Extract topics from page text. Returns ranked list of (topic, score)."""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Identifier returned in API response so callers know which classifier ran."""
        pass
