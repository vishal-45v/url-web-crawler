import httpx
import json
from typing import List
from app.models.schemas import Topic
from app.services.classifier.base import BaseClassifier
from app.config.settings import settings

_PROMPT = """\
You are a content classifier. Extract the top 10 most relevant topics from this web page.

Title: {title}
Content (truncated): {content}

Return ONLY a valid JSON array — no explanation, no markdown, no extra text.
Format: [{{"topic": "...", "score": 0.0}}]
Score range: 0.0 (irrelevant) to 1.0 (highly relevant). Rank by score descending.\
"""


class OllamaClassifier(BaseClassifier):
    """
    LLM-based topic classification via a self-hosted Ollama instance.
    Free to run — no API costs. Requires Ollama running at OLLAMA_BASE_URL.
    Switch to this from KeyBERT: set CLASSIFIER=ollama in .env.
    """

    @property
    def name(self) -> str:
        return f"ollama:{settings.ollama_model}"

    async def classify(self, text: str, title: str = "") -> List[Topic]:
        prompt = _PROMPT.format(title=title, content=text[:3000])

        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{settings.ollama_base_url}/api/generate",
                json={
                    "model": settings.ollama_model,
                    "prompt": prompt,
                    "stream": False,
                },
            )
            response.raise_for_status()

        raw = response.json().get("response", "[]")

        # Robustly extract JSON array even if the model wraps it in explanation text
        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start == -1 or end == 0:
            return []

        items = json.loads(raw[start:end])
        return [
            Topic(topic=item["topic"], score=float(item.get("score", 0.0)))
            for item in items
            if item.get("topic")
        ]
