from __future__ import annotations

from langchain_openai import ChatOpenAI

from .config import Settings


def build_llm(settings: Settings) -> ChatOpenAI:
    provider = settings.llm_provider.strip().lower()

    if provider == "minimax":
        api_key = settings.minimax_api_key.strip()
        base_url = settings.minimax_base_url.strip()
        model = settings.minimax_model.strip()
        if not api_key:
            raise ValueError("LLM_PROVIDER=minimax 时，MINIMAX_API_KEY 未配置，请先在 .env 中填写。")
        return ChatOpenAI(
            api_key=api_key,
            base_url=base_url,
            model=model,
            temperature=0.2,
        )

    if provider == "openai":
        api_key = settings.openai_api_key.strip()
        base_url = settings.openai_base_url.strip()
        model = settings.openai_model.strip()
        if not api_key:
            raise ValueError("LLM_PROVIDER=openai 时，OPENAI_API_KEY 未配置，请先在 .env 中填写。")
        return ChatOpenAI(
            api_key=api_key,
            base_url=base_url,
            model=model,
            temperature=0.2,
        )

    raise ValueError(f"不支持的 LLM_PROVIDER: {settings.llm_provider}。当前仅支持 minimax / openai。")
