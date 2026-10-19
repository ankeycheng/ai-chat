from app.core.config import Settings
from app.infra.llm.base import ChatClient


def create_chat_client(settings: Settings) -> ChatClient:
    if settings.llm_provider == "fake":
        from app.infra.llm.fake import FakeChatClient

        return FakeChatClient()
    from app.infra.llm.deepseek import DeepSeekChatClient

    return DeepSeekChatClient(
        settings.deepseek_api_key.get_secret_value(),
        settings.deepseek_base_url,
        settings.upstream_connect_timeout_seconds,
        settings.upstream_read_timeout_seconds,
    )
