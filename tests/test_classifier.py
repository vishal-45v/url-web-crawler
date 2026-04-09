"""
Unit tests for the classifier layer.

Tests cover:
  - KeyBERTClassifier output shape and types
  - Topic score rounding (4 decimal places)
  - Classifier name property
  - Factory singleton behaviour (same instance returned on repeated calls)
  - Factory raises ValueError for unknown classifier names
"""
import pytest
from unittest.mock import MagicMock, patch

from app.models.schemas import Topic
from app.services.classifier.keybert_classifier import KeyBERTClassifier
from app.services.classifier import factory


# ---------------------------------------------------------------------------
# KeyBERTClassifier
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_registry():
    """Reset the singleton registry before each test for isolation."""
    factory._registry.clear()
    yield
    factory._registry.clear()


async def test_keybert_returns_list_of_topics():
    classifier = KeyBERTClassifier()
    mock_model = MagicMock()
    mock_model.extract_keywords.return_value = [
        ("compact toaster", 0.9134),
        ("kitchen appliance", 0.8720),
        ("2-slice toaster", 0.8401),
    ]
    classifier._model = mock_model

    topics = await classifier.classify("A compact toaster for kitchen use", "Toaster Review")

    assert len(topics) == 3
    assert all(isinstance(t, Topic) for t in topics)


async def test_keybert_topic_fields_correct():
    classifier = KeyBERTClassifier()
    mock_model = MagicMock()
    mock_model.extract_keywords.return_value = [("compact toaster", 0.91345678)]
    classifier._model = mock_model

    topics = await classifier.classify("text", "title")

    assert topics[0].topic == "compact toaster"
    assert topics[0].score == round(0.91345678, 4)


async def test_keybert_scores_rounded_to_4_decimal_places():
    classifier = KeyBERTClassifier()
    mock_model = MagicMock()
    mock_model.extract_keywords.return_value = [("topic", 0.123456789)]
    classifier._model = mock_model

    topics = await classifier.classify("text", "title")

    assert topics[0].score == 0.1235  # rounded to 4 decimal places


async def test_keybert_empty_keyword_list():
    classifier = KeyBERTClassifier()
    mock_model = MagicMock()
    mock_model.extract_keywords.return_value = []
    classifier._model = mock_model

    topics = await classifier.classify("", "")
    assert topics == []


def test_keybert_classifier_name():
    assert KeyBERTClassifier().name == "keybert"


async def test_keybert_prepends_title_to_text():
    """Title should be prepended to text so KeyBERT has richer context."""
    classifier = KeyBERTClassifier()
    mock_model = MagicMock()
    mock_model.extract_keywords.return_value = []
    classifier._model = mock_model

    await classifier.classify(text="body content", title="Page Title")

    call_args = mock_model.extract_keywords.call_args[0][0]
    assert call_args.startswith("Page Title.")
    assert "body content" in call_args


async def test_keybert_body_truncated_at_2500_chars():
    """Large body text should be capped at 2500 chars before classification."""
    classifier = KeyBERTClassifier()
    mock_model = MagicMock()
    mock_model.extract_keywords.return_value = []
    classifier._model = mock_model

    long_text = "word " * 1000  # >> 2500 chars

    await classifier.classify(text=long_text, title="T")

    call_args = mock_model.extract_keywords.call_args[0][0]
    # Title prefix + 2500 char body cap
    assert len(call_args) <= len("T. ") + 2500 + 5  # small buffer for prefix


# ---------------------------------------------------------------------------
# Classifier Factory
# ---------------------------------------------------------------------------

def test_factory_returns_keybert_classifier():
    with patch("app.services.classifier.factory.settings") as mock_settings:
        mock_settings.classifier = "keybert"
        classifier = factory.get_classifier()
        assert isinstance(classifier, KeyBERTClassifier)


def test_factory_singleton_same_instance_returned():
    with patch("app.services.classifier.factory.settings") as mock_settings:
        mock_settings.classifier = "keybert"
        c1 = factory.get_classifier()
        c2 = factory.get_classifier()
        assert c1 is c2


def test_factory_raises_for_unknown_classifier():
    with patch("app.services.classifier.factory.settings") as mock_settings:
        mock_settings.classifier = "gpt-unknown"
        with pytest.raises(ValueError, match="Unknown classifier"):
            factory.get_classifier()


def test_factory_error_message_includes_classifier_name():
    with patch("app.services.classifier.factory.settings") as mock_settings:
        mock_settings.classifier = "bad_name"
        with pytest.raises(ValueError, match="bad_name"):
            factory.get_classifier()
