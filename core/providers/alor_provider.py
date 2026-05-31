"""
Модуль подключения к Alor API.

Реализует MarketDataProvider и OrderProvider для работы через
Alor Open API (REST + WebSocket v2).

Версия для Этапа 1: ЗАГЛУШКА.
Полная реализация — Этап 4 (Хеджер и риски), когда потребуется
боевое/демо подключение к брокеру.

Особенности Alor API:
    - JWT-авторизация (refresh token → access token)
    - REST для торговых операций
    - WebSocket v2 для потоковых котировок
    - Два эндпоинта: api.alor.ru (боевой) и apidev.alor.ru (демо)
"""

import logging
from typing import Any, Dict, List, Optional

from core.event_bus import EventBus
# Будут импортированы при реализации:
# from core.providers.market_data import (MarketDataProvider, OrderProvider,
#     Quote, OrderRequest, OrderInfo, OptionInstrument, Position)

logger = logging.getLogger(__name__)


class AlorDataProvider:
    """
    Провайдер данных Alor API (заглушка для Этапа 1).

    Полная реализация будет включать:
        - JWT-аутентификацию
        - REST-запросы котировок и опционных досок
        - WebSocket-подписку на потоковые данные
        - Автоматический реконнект с экспоненциальной задержкой
    """

    def __init__(self, event_bus: EventBus, config: Dict[str, Any]):
        self._event_bus = event_bus
        self._config = config
        self._connected = False
        logger.info("AlorDataProvider: ЗАГЛУШКА (реализация — Этап 4)")


class AlorOrderProvider:
    """
    Провайдер ордеров Alor API (заглушка для Этапа 1).

    Полная реализация будет включать:
        - REST: выставление, изменение, отмена ордеров
        - WebSocket: подписка на изменения ордеров и позиций
        - Получение текущих позиций по счёту
    """

    def __init__(self, event_bus: EventBus, config: Dict[str, Any]):
        self._event_bus = event_bus
        self._config = config
        self._connected = False
        logger.info("AlorOrderProvider: ЗАГЛУШКА (реализация — Этап 4)")
