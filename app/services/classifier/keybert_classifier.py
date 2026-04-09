from keybert import KeyBERT
from typing import List
from app.models.schemas import Topic
from app.services.classifier.base import BaseClassifier


class KeyBERTClassifier(BaseClassifier):
    """
    Semantic keyword extraction using KeyBERT + sentence-transformers.
    Model: all-MiniLM-L6-v2 (~90MB on disk, ~200MB RAM when loaded).
    Lazy-loaded on first request to keep startup fast.
    """

    def __init__(self):
        self._model: KeyBERT | None = None

    @property
    def name(self) -> str:
        return "keybert"

    def _load(self):
        if self._model is None:
            self._model = KeyBERT()

    async def classify(self, text: str, title: str = "") -> List[Topic]:
        self._load()

        # Prepend title for better context; cap body at 2500 chars for speed
        combined = f"{title}. {text[:2500]}"

        keywords = self._model.extract_keywords(
            combined,
            keyphrase_ngram_range=(1, 3),   # single words up to 3-word phrases
            stop_words="english",
            use_maxsum=True,                 # maximize diversity in results
            nr_candidates=20,
            top_n=10,
        )

        return [Topic(topic=kw, score=round(score, 4)) for kw, score in keywords]
