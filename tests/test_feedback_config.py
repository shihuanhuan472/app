import pytest

from feedback_learning.config import feedback_enabled, feedback_learning_enabled
from feedback_learning.repository import FeedbackLearningRepository
from feedback_learning.router import _ensure_feedback_enabled
from utils.app_exceptions import AppException


def test_feedback_defaults_preserve_collection_and_disable_learning(monkeypatch):
    monkeypatch.delenv("FEEDBACK_ENABLED", raising=False)
    monkeypatch.delenv("FEEDBACK_LEARNING_ENABLED", raising=False)

    assert feedback_enabled() is True
    assert feedback_learning_enabled() is False


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "unexpected"])
def test_feedback_total_switch_disables_collection(monkeypatch, value):
    monkeypatch.setenv("FEEDBACK_ENABLED", value)
    monkeypatch.setenv("FEEDBACK_LEARNING_ENABLED", "true")

    assert feedback_enabled() is False
    assert feedback_learning_enabled() is False


def test_learning_switch_is_independent_when_collection_is_enabled(monkeypatch):
    monkeypatch.setenv("FEEDBACK_ENABLED", "true")
    monkeypatch.setenv("FEEDBACK_LEARNING_ENABLED", "true")
    assert feedback_learning_enabled() is True

    monkeypatch.setenv("FEEDBACK_LEARNING_ENABLED", "false")
    assert feedback_enabled() is True
    assert feedback_learning_enabled() is False


def test_feedback_route_guard_rejects_when_disabled(monkeypatch):
    monkeypatch.setenv("FEEDBACK_ENABLED", "false")

    with pytest.raises(AppException) as exc:
        _ensure_feedback_enabled()

    assert exc.value.http_status == 403
    assert exc.value.message == "反馈功能已关闭"


@pytest.mark.asyncio
async def test_disabled_feedback_is_never_eligible(monkeypatch):
    monkeypatch.setenv("FEEDBACK_ENABLED", "false")
    repository = FeedbackLearningRepository(db=None)

    assert await repository.is_feedback_eligible(message_id=20, conversation_id=10) is False
