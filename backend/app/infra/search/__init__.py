from app.core.config import Settings
from app.infra.search.base import SearchProvider


def create_search_provider(settings: Settings) -> SearchProvider:
    if settings.search_provider == "fake":
        from app.infra.search.fake import FakeSearchProvider

        return FakeSearchProvider()
    if settings.search_provider == "disabled":
        from app.infra.search.fake import DisabledSearchProvider

        return DisabledSearchProvider()
    from app.infra.search.deepseek import DeepSeekSearchProvider

    return DeepSeekSearchProvider(
        settings.deepseek_api_key.get_secret_value(),
        settings.deepseek_base_url,
        settings.search_timeout_seconds,
    )
