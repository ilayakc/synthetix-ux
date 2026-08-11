from app.services.ai_pipeline.errors import ERROR_BANNED_CLAIM, ERROR_INVALID_PAYLOAD, AIPipelineError
from app.services.ai_pipeline.retry_policy import is_retryable_error


def test_banned_claim_is_retryable_once_by_worker_policy():
    assert is_retryable_error(AIPipelineError(ERROR_BANNED_CLAIM, "safe message")) is True


def test_other_validation_errors_remain_terminal():
    assert is_retryable_error(AIPipelineError(ERROR_INVALID_PAYLOAD, "safe message")) is False
