import boto3
import botocore.config
from pydantic_ai.models import bedrock as bedrock_models
from pydantic_ai.providers import bedrock as bedrock_providers

from app import config

settings = config.get_config()

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

provider = bedrock_providers.BedrockProvider(
    bedrock_client=boto3.client(
        "bedrock-runtime", region_name=settings.aws_region, config=client_config
    )
)


def _setup_model(
    model_config: config.BedrockModelConfig,
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
        provider=provider,
        settings=build_settings(),
    )


claude_sonnet = _setup_model(settings.bedrock.claude_sonnet)  # type: ignore[attr-defined]
