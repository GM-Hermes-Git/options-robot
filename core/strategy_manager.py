"""
Управление стратегиями (StrategyManager).

Отвечает за жизненный цикл стратегий: создание, запуск, остановка, мониторинг.
Хранит параметры стратегий и их текущий статус.

Статусы стратегии (из ТЗ, раздел 4.3):
    CONFIGURED    — параметры заданы, стратегия не запущена
    WAITING       — запущена, ожидает наступления даты/времени начала
    ACTIVE        — мониторинг цены БА, ожидание триггера
    TRIGGERED     — триггер сработал, ордера выставлены в стакан
    BUILDING      — идёт набор позиции (частичное исполнение)
    POSITION_OPEN — позиция набрана полностью, хеджер активен
    CLOSING       — идёт закрытие позиции (SL/TP или ручное)
    STOPPED       — стратегия остановлена

Версия для Этапа 1: ЗАГЛУШКА.
Полная реализация — Этап 2 (Ядро стратегий).
"""

import logging
from enum import Enum

logger = logging.getLogger(__name__)


class StrategyStatus(Enum):
    """Статусы жизненного цикла стратегии."""
    CONFIGURED = "CONFIGURED"
    WAITING = "WAITING"
    ACTIVE = "ACTIVE"
    TRIGGERED = "TRIGGERED"
    BUILDING = "BUILDING"
    POSITION_OPEN = "POSITION_OPEN"
    CLOSING = "CLOSING"
    STOPPED = "STOPPED"


class StrategyManager:
    """
    Менеджер стратегий (заглушка для Этапа 1).

    Полная реализация будет включать:
        - CRUD операций над стратегиями
        - Управление жизненным циклом
        - Загрузку/сохранение конфигураций
        - Параллельную работу нескольких стратегий
        - Валидацию параметров
    """

    def __init__(self, event_bus: "EventBus", config: dict):  # noqa: F821
        self._event_bus = event_bus
        self._config = config
        self._strategies: dict = {}
        logger.info("StrategyManager: ЗАГЛУШКА (реализация — Этап 2)")
