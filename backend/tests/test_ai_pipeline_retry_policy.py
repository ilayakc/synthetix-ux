import pytest

from app.services.ai_pipeline.errors import (
    ERROR_BANNED_CLAIM,
    ERROR_INVALID_AGGREGATION,
    ERROR_INVALID_EVIDENCE_REFERENCE,
    ERROR_INVALID_PAYLOAD,
    AIPipelineError,
)
from app.services.ai_pipeline.retry_policy import is_retryable_error


@pytest.mark.parametrize(
    "code", [ERROR_BANNED_CLAIM, ERROR_INVALID_AGGREGATION, ERROR_INVALID_EVIDENCE_REFERENCE]
)
def test_repairable_model_validation_errors_are_retryable_once_by_worker_policy(code):
    assert is_retryable_error(AIPipelineError(code, "safe message")) is True


def test_other_validation_errors_remain_terminal():
    assert is_retryable_error(AIPipelineError(ERROR_INVALID_PAYLOAD, "safe message")) is False
