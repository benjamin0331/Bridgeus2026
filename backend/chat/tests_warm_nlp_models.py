from io import StringIO
from unittest.mock import patch

from django.core.management import call_command


def test_warm_nlp_models_loads_embedding_and_emotion_models():
    stdout = StringIO()

    with patch(
        "chat.management.commands.warm_nlp_models.get_embedding",
        return_value=[0.0] * 384,
    ) as get_embedding, patch(
        "chat.management.commands.warm_nlp_models.analyze_emotion",
        return_value={"score": 0.0, "label": "neutral", "is_over_threshold": False},
    ) as analyze_emotion:
        call_command("warm_nlp_models", stdout=stdout)

    get_embedding.assert_called_once()
    analyze_emotion.assert_called_once()
    assert "Embedding model ready" in stdout.getvalue()
    assert "Emotion model ready" in stdout.getvalue()


def test_warm_nlp_models_can_skip_emotion_model():
    stdout = StringIO()

    with patch(
        "chat.management.commands.warm_nlp_models.get_embedding",
        return_value=[0.0] * 384,
    ) as get_embedding, patch(
        "chat.management.commands.warm_nlp_models.analyze_emotion",
    ) as analyze_emotion:
        call_command("warm_nlp_models", "--skip-emotion", stdout=stdout)

    get_embedding.assert_called_once()
    analyze_emotion.assert_not_called()
    assert "Emotion model skipped" in stdout.getvalue()
