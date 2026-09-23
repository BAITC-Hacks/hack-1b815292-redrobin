"""Small, retrying wrapper around OpenAI Responses Structured Outputs."""

from __future__ import annotations

import time
from collections.abc import Callable
from threading import Lock
from typing import Any, TypeVar

import openai
from openai import OpenAI
from pydantic import BaseModel, ValidationError

from app.config import Settings, get_settings
from app.errors import AppError

SchemaT = TypeVar("SchemaT", bound=BaseModel)
TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
RETRY_DELAYS = (2.0, 4.0)


class AIClient:
    """Return validated models while hiding provider responses and secrets."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        sdk_client: Any = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings or get_settings()
        self._sdk_client = sdk_client
        self._sleep = sleep
        self._client_lock = Lock()

    def parse(
        self,
        messages: list[dict[str, str]],
        schema: type[SchemaT],
    ) -> SchemaT:
        if not self.settings.openai_api_key or not self.settings.openai_model:
            raise self._unavailable()

        client = self._client()
        transient_attempt = 0
        schema_attempt = 0
        current_messages = list(messages)
        while True:
            try:
                response = client.responses.parse(
                    model=self.settings.openai_model,
                    input=current_messages,
                    text_format=schema,
                    store=False,
                )
                self._ensure_complete(response)
                parsed = getattr(response, "output_parsed", None)
                if parsed is None:
                    raise ValidationError.from_exception_data(
                        schema.__name__,
                        [
                            {
                                "type": "value_error",
                                "loc": ("output_parsed",),
                                "input": None,
                                "ctx": {"error": ValueError("missing parsed output")},
                            }
                        ],
                    )
                return parsed
            except ValidationError as exc:
                if schema_attempt == 0:
                    schema_attempt += 1
                    current_messages = [
                        *current_messages,
                        {
                            "role": "developer",
                            "content": (
                                "Предыдущий ответ не прошел строгую схему. "
                                "Исправь структуру, не добавляя фактов или ID."
                            ),
                        },
                    ]
                    continue
                raise self._invalid_response() from exc
            except AppError:
                raise
            except (
                openai.APITimeoutError,
                openai.APIConnectionError,
            ) as exc:
                if transient_attempt < len(RETRY_DELAYS):
                    self._sleep(RETRY_DELAYS[transient_attempt])
                    transient_attempt += 1
                    continue
                raise self._unavailable() from exc
            except openai.APIStatusError as exc:
                if (
                    exc.status_code in TRANSIENT_STATUS_CODES
                    and transient_attempt < len(RETRY_DELAYS)
                ):
                    self._sleep(RETRY_DELAYS[transient_attempt])
                    transient_attempt += 1
                    continue
                raise self._unavailable() from exc
            except (TypeError, ValueError) as exc:
                if schema_attempt == 0:
                    schema_attempt += 1
                    current_messages = [
                        *current_messages,
                        {
                            "role": "developer",
                            "content": "Верни ответ строго по заданной схеме.",
                        },
                    ]
                    continue
                raise self._invalid_response() from exc
            except Exception as exc:
                # Never expose provider exception text: it can contain request data.
                raise self._unavailable() from exc

    def _client(self) -> Any:
        with self._client_lock:
            if self._sdk_client is None:
                self._sdk_client = OpenAI(
                    api_key=self.settings.openai_api_key,
                    timeout=60.0,
                    max_retries=0,
                )
        return self._sdk_client

    def _ensure_complete(self, response: Any) -> None:
        status = getattr(response, "status", None)
        if status == "incomplete":
            raise self._unavailable()
        for item in getattr(response, "output", []) or []:
            for content in getattr(item, "content", []) or []:
                if getattr(content, "type", None) == "refusal":
                    raise self._invalid_response()

    @staticmethod
    def _unavailable() -> AppError:
        return AppError(
            503,
            "ai_unavailable",
            "Сервис анализа временно недоступен. Извлеченные пункты сохранены.",
            retryable=True,
        )

    @staticmethod
    def _invalid_response() -> AppError:
        return AppError(
            502,
            "invalid_ai_response",
            "Ответ сервиса анализа не прошел проверку структуры.",
            retryable=True,
        )


ai_client = AIClient()
