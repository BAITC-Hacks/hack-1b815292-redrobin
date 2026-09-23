"""OpenAI client behavior with provider calls fully mocked."""

from types import SimpleNamespace

import httpx
import openai
import pytest

from app.ai.client import AIClient
from app.ai.schemas import ConclusionResponse
from app.config import Settings
from app.errors import AppError


def settings() -> Settings:
    return Settings(
        _env_file=None,
        OPENAI_API_KEY="unit-test-key",
        OPENAI_MODEL="unit-test-model",
    )


class Responses:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def response(parsed=None, *, status="completed", output=None):
    return SimpleNamespace(
        output_parsed=parsed,
        status=status,
        output=output or [],
    )


def test_client_returns_output_parsed_and_disables_storage() -> None:
    parsed = ConclusionResponse(
        summary="Кратко",
        key_finding_ids=[],
        recommendations=[],
    )
    responses = Responses([response(parsed)])
    client = AIClient(
        settings(),
        sdk_client=SimpleNamespace(responses=responses),
        sleep=lambda _: None,
    )

    result = client.parse([{"role": "user", "content": "test"}], ConclusionResponse)

    assert result == parsed
    assert responses.calls[0]["model"] == "unit-test-model"
    assert responses.calls[0]["store"] is False
    assert responses.calls[0]["text_format"] is ConclusionResponse


def test_client_retries_429_with_two_documented_delays() -> None:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    rate_limit = openai.RateLimitError(
        "rate limited",
        response=httpx.Response(429, request=request),
        body=None,
    )
    parsed = ConclusionResponse(summary="Готово")
    responses = Responses([rate_limit, rate_limit, response(parsed)])
    delays = []
    client = AIClient(
        settings(), sdk_client=SimpleNamespace(responses=responses), sleep=delays.append
    )

    assert client.parse([], ConclusionResponse) == parsed
    assert delays == [2.0, 4.0]


def test_client_retries_schema_once_then_returns_controlled_error() -> None:
    responses = Responses([response(), response()])
    client = AIClient(
        settings(),
        sdk_client=SimpleNamespace(responses=responses),
        sleep=lambda _: None,
    )

    with pytest.raises(AppError) as error:
        client.parse([], ConclusionResponse)

    assert error.value.code == "invalid_ai_response"
    assert len(responses.calls) == 2


@pytest.mark.parametrize("status_code", [401, 404, 500, 502, 503, 504])
def test_client_maps_provider_status_to_ai_unavailable(status_code: int) -> None:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    error = openai.APIStatusError(
        "provider error",
        response=httpx.Response(status_code, request=request),
        body=None,
    )
    attempts = 3 if status_code >= 500 else 1
    responses = Responses([error] * attempts)
    client = AIClient(
        settings(),
        sdk_client=SimpleNamespace(responses=responses),
        sleep=lambda _: None,
    )

    with pytest.raises(AppError) as caught:
        client.parse([], ConclusionResponse)
    assert caught.value.code == "ai_unavailable"
    assert "provider error" not in caught.value.message
