import os

# AppConfig environment defaults (use the same names pydantic-settings will look up)
os.environ.setdefault("PYTHON_ENV", "test")
os.environ.setdefault("AWS_REGION", "eu-west-2")
os.environ.setdefault("HOST", "127.0.0.1")
os.environ.setdefault("PORT", "8086")
os.environ.setdefault("LOG_CONFIG", "logging-dev.json")
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017")
os.environ.setdefault("MONGO_DATABASE", "ai-uc-content-swarm-runtime")
os.environ.setdefault("MONGO_TRUSTSTORE", "TRUSTSTORE_CDP_ROOT_CA")
os.environ.setdefault("LOCALSTACK_ENDPOINT_URL", "")
os.environ.setdefault("ENABLE_METRICS", "false")
