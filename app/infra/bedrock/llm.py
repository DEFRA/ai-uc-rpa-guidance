import logging
import time
from collections.abc import Callable
from typing import Any

import boto3
import botocore.config
import botocore.exceptions
from pydantic_ai.models import bedrock as bedrock_models
from pydantic_ai.providers import bedrock as bedrock_providers

from app import config

settings = config.get_config()

logger = logging.getLogger(__name__)

# A pooled connection can die without either end hearing of it: the network
# drops, the computer sleeps, or something on the way forgets an idle
# connection. A call sent on one waits for a timeout, then retries on a new
# connection. Replacing the client after this long unused leaves its old
# connections behind, so the next call opens a new one.
IDLE_SECONDS = 300

# boto3 runs each call in a thread, which cancelling its task does not stop: a
# cancelled index rebuild's calls run on and keep their connections. The
# default pool of 10 then leaves the next rebuild's calls queuing for one.
# The read timeout is pydantic-ai's own default, kept because passing a client
# replaces it: one model pass over a long guide takes minutes.
client_config = botocore.config.Config(
    read_timeout=300,
    max_pool_connections=25,
    retries={"mode": "standard", "total_max_attempts": 3},
)

# Bedrock sends nothing until the reply is complete, so the read timeout must
# outlast the slowest search call (21s seen); a dead connection then costs a
# minute, not five. Sending a request also runs under the connect timeout.
search_client_config = botocore.config.Config(
    connect_timeout=10,
    read_timeout=60,
    max_pool_connections=25,
    retries={"mode": "standard", "total_max_attempts": 2},
)

# The failures botocore counts as the connection's, not Bedrock's reply's.
CONNECTION_FAILURES = (
    botocore.exceptions.ConnectionError,
    botocore.exceptions.HTTPClientError,
)


def _log_failed_attempt(
    attempts: int,
    operation: Any,
    response: Any = None,
    caught_exception: Exception | None = None,
    **_kwargs: Any,
) -> None:
    """Log an attempt at a Bedrock call that failed; botocore decides the retry."""
    if caught_exception is not None:
        failure = type(caught_exception).__name__
    elif response is not None and response[0].status_code >= 400:
        failure = f"HTTP {response[0].status_code}"
    else:
        return
    logger.warning(
        "[Bedrock] %s attempt %d failed: %s", operation.name, attempts, failure
    )


def _new_client(client_settings: botocore.config.Config) -> Any:
    """Create a Bedrock Runtime client that logs each failed attempt.

    A connection that times out or breaks has died with the network path, and
    so have the others in the pool: the client closes them all, so the retry
    and later calls open new ones instead of each waiting out a timeout.
    """
    client = boto3.client(
        "bedrock-runtime", region_name=settings.aws_region, config=client_settings
    )

    def close_connections_after_connection_failure(
        caught_exception: Exception | None = None, **_kwargs: Any
    ) -> None:
        if isinstance(caught_exception, CONNECTION_FAILURES):
            client.close()

    client.meta.events.register("needs-retry.bedrock-runtime", _log_failed_attempt)
    client.meta.events.register(
        "needs-retry.bedrock-runtime", close_connections_after_connection_failure
    )
    return client


class FreshAfterIdleProvider(bedrock_providers.BedrockProvider):
    """A Bedrock provider that replaces its client once it has gone unused.

    pydantic-ai reads the client at the start of every call. A call already
    running keeps the client it started with.
    """

    def __init__(
        self,
        new_client: Callable[[], Any],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create the provider with its first client.

        Args:
            new_client: Makes a new Bedrock Runtime client.
            clock: Gives the time in seconds; replaced in tests.
        """
        super().__init__(bedrock_client=new_client())
        self._new_client = new_client
        self._clock = clock
        self._last_used = clock()

    @property
    def client(self) -> Any:
        """The client, replaced first if it has gone unused too long."""
        now = self._clock()
        if now - self._last_used > IDLE_SECONDS:
            self._client = self._new_client()
        self._last_used = now
        return self._client

    @client.setter
    def client(self, client: Any) -> None:
        self._client = client


provider = FreshAfterIdleProvider(lambda: _new_client(client_config))
search_provider = FreshAfterIdleProvider(lambda: _new_client(search_client_config))


def _setup_model(
    model_config: config.BedrockModelConfig,
    model_provider: FreshAfterIdleProvider,
) -> bedrock_models.BedrockConverseModel:
    """Create a BedrockConverseModel from configuration."""

    def build_settings() -> bedrock_models.BedrockModelSettings:
        settings = bedrock_models.BedrockModelSettings(
            bedrock_inference_profile=model_config.inference_profile,
            temperature=0.0,
        )

        if model_config.guardrails:
            settings["bedrock_guardrail_config"] = {
                "guardrailIdentifier": model_config.guardrails.id,
                "guardrailVersion": model_config.guardrails.version,
                "trace": "enabled",
            }

        return settings

    model_name = model_config.model_id

    return bedrock_models.BedrockConverseModel(
        model_name,
        provider=model_provider,
        settings=build_settings(),
    )


claude_sonnet = _setup_model(settings.bedrock.claude_sonnet, provider)  # type: ignore[attr-defined]
claude_sonnet_search = _setup_model(settings.bedrock.claude_sonnet, search_provider)  # type: ignore[attr-defined]
