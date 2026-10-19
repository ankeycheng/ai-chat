from app.core.config import Settings
from app.infra.broker.base import Broker


def create_broker(settings: Settings) -> Broker:
    if settings.broker_backend == "redis":
        from app.infra.broker.redis_broker import RedisBroker

        return RedisBroker(settings.redis_url, settings.redis_max_connections)
    from app.infra.broker.memory_broker import MemoryBroker

    return MemoryBroker()
