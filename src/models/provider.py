"""
Layer 4 — Model Layer: Cloud-Agnostic LLM Provider
Switch between Azure OpenAI, OpenAI, and AWS Bedrock via the
LLM_PROVIDER environment variable — no code changes required.
"""
from functools import lru_cache
from langchain_core.language_models import BaseChatModel
from src.config.settings import Settings


class ModelProvider:
    """
    Returns LangChain BaseChatModel instances for two tiers:
      heavy — used for planning (needs stronger reasoning)
      light — used for classification, inference, chat responses
    Models are cached after first creation.
    """

    def __init__(self, settings: Settings):
        self._s = settings
        self._cache: dict[str, BaseChatModel] = {}

    def heavy(self) -> BaseChatModel:
        return self._get("heavy")

    def light(self) -> BaseChatModel:
        return self._get("light")

    def _get(self, tier: str) -> BaseChatModel:
        key = f"{self._s.llm_provider}:{tier}"
        if key not in self._cache:
            self._cache[key] = self._build(tier)
        return self._cache[key]

    def _build(self, tier: str) -> BaseChatModel:
        s = self._s

        if s.llm_provider == "azure_openai":
            from langchain_openai import AzureChatOpenAI
            deployment = s.azure_deployment_heavy if tier == "heavy" else s.azure_deployment_light
            return AzureChatOpenAI(
                azure_deployment=deployment,
                azure_endpoint=s.azure_openai_endpoint,
                api_key=s.azure_openai_api_key,
                api_version=s.azure_openai_api_version,
                temperature=0,
                max_retries=3,
            )

        if s.llm_provider == "bedrock":
            import boto3
            from langchain_aws import ChatBedrock
            model_id = s.bedrock_model_heavy if tier == "heavy" else s.bedrock_model_light
            return ChatBedrock(
                model_id=model_id,
                client=boto3.client("bedrock-runtime", region_name=s.aws_region),
                model_kwargs={"temperature": 0, "max_tokens": 4096},
            )

        # Default: OpenAI
        from langchain_openai import ChatOpenAI
        model = s.openai_model_heavy if tier == "heavy" else s.openai_model_light
        return ChatOpenAI(
            model=model,
            api_key=s.openai_api_key,
            temperature=0,
            max_retries=3,
        )
