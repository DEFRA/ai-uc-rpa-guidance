"""Tests for the Bedrock LLM module."""

import importlib
import logging
from typing import Any
from unittest.mock import Mock

import botocore.exceptions
import pytest

from app.infra.bedrock import llm


class TestClaudeSonnet:
    """Test claude_sonnet module-level export."""

    @pytest.fixture(autouse=True)
    def restore_module(self):
        yield
        importlib.reload(llm)

    def test_created_without_guardrails(self) -> None:
        assert "bedrock_guardrail_config" not in llm.claude_sonnet.settings

    def test_created_with_guardrails(self, mocker) -> None:
        mock_config = mocker.Mock()
        mock_config.aws_region = "eu-west-2"
        mock_config.bedrock.claude_sonnet.model_id = "anthropic.claude-sonnet-4-6"
        mock_config.bedrock.claude_sonnet.inference_profile = (
            "arn:aws:bedrock:eu-west-2:123456789012:application-inference-profile/test"
        )
        mock_config.bedrock.claude_sonnet.guardrails.id = "guardrail-123"
        mock_config.bedrock.claude_sonnet.guardrails.version = "1"

        mocker.patch("app.config.get_config", return_value=mock_config)
        importlib.reload(llm)

        assert llm.claude_sonnet.settings["bedrock_guardrail_config"] == {
            "guardrailIdentifier": "guardrail-123",
            "guardrailVersion": "1",
            "trace": "enabled",
        }


class TestBedrockClient:
    """Test the client every Bedrock call goes through."""

    def test_waits_for_long_replies(self) -> None:
        assert llm.provider.client.meta.config.read_timeout == 300

    def test_holds_more_calls_than_a_rebuild_makes_at_once(self) -> None:
        assert llm.provider.client.meta.config.max_pool_connections == 25

    def test_retries_a_failed_call_at_most_twice(self) -> None:
        assert llm.provider.client.meta.config.retries == {
            "mode": "standard",
            "total_max_attempts": 3,
        }


class TestSearchClient:
    """Test the client the AI search goes through: it gives up on a dead connection soon."""

    def test_gives_up_opening_a_connection_after_10_seconds(self) -> None:
        assert llm.search_provider.client.meta.config.connect_timeout == 10

    def test_gives_up_waiting_for_a_reply_after_a_minute(self) -> None:
        assert llm.search_provider.client.meta.config.read_timeout == 60

    def test_tries_a_failed_call_once_more(self) -> None:
        assert llm.search_provider.client.meta.config.retries == {
            "mode": "standard",
            "total_max_attempts": 2,
        }

    def test_is_the_one_the_search_model_uses(self) -> None:
        assert llm.claude_sonnet_search.client is llm.search_provider.client


class FakeClock:
    """A clock the test moves on by hand."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class TestFreshClientAfterIdle:
    """Test that a client left unused is replaced, so its pooled connections are not."""

    @pytest.fixture
    def clock(self) -> FakeClock:
        return FakeClock()

    @pytest.fixture
    def provider(self, clock: FakeClock) -> llm.FreshAfterIdleProvider:
        return llm.FreshAfterIdleProvider(Mock, clock=clock)

    def test_keeps_its_client_within_5_minutes_of_the_last_use(
        self, provider: llm.FreshAfterIdleProvider, clock: FakeClock
    ) -> None:
        first = provider.client
        clock.now = 300

        assert provider.client is first

    def test_replaces_its_client_after_5_minutes_without_use(
        self, provider: llm.FreshAfterIdleProvider, clock: FakeClock
    ) -> None:
        first = provider.client
        clock.now = 301

        assert provider.client is not first

    def test_counts_the_idle_time_from_the_last_use(
        self, provider: llm.FreshAfterIdleProvider, clock: FakeClock
    ) -> None:
        first = provider.client
        clock.now = 200
        _ = provider.client
        clock.now = 400

        assert provider.client is first

    @pytest.mark.parametrize("name", ["provider", "search_provider"])
    def test_every_bedrock_client_is_replaced_after_idle(self, name: str) -> None:
        assert isinstance(getattr(llm, name), llm.FreshAfterIdleProvider)


def _emit_attempt(client: Any, **outcome: Any) -> None:
    """Tell the client's retry handlers how an attempt at a Converse call ended."""
    client.meta.events.emit(
        "needs-retry.bedrock-runtime.Converse",
        endpoint=Mock(),
        operation=client.meta.service_model.operation_model("Converse"),
        attempts=1,
        request_dict={"context": {}},
        **({"response": None, "caught_exception": None} | outcome),
    )


def _reply(status: int) -> tuple[Mock, dict[str, Any]]:
    return Mock(status_code=status), {}


class TestRetryLog:
    """Test that every failed attempt at a Bedrock call is logged."""

    @pytest.fixture(params=["provider", "search_provider"])
    def client(self, request: pytest.FixtureRequest) -> Any:
        return getattr(llm, request.param).client

    def test_logs_a_failed_connection(
        self, client: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        dead = botocore.exceptions.ReadTimeoutError(endpoint_url="https://bedrock")

        with caplog.at_level(logging.WARNING, logger=llm.__name__):
            _emit_attempt(client, caught_exception=dead)

        assert "Converse attempt 1 failed: ReadTimeoutError" in caplog.text

    def test_logs_an_error_reply(
        self, client: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=llm.__name__):
            _emit_attempt(client, response=_reply(503))

        assert "Converse attempt 1 failed: HTTP 503" in caplog.text

    def test_logs_nothing_for_a_call_that_worked(
        self, client: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=llm.__name__):
            _emit_attempt(client, response=_reply(200))

        assert caplog.text == ""
