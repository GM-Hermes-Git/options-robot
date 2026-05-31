"""
Внутренняя шина событий (Event Bus).

Центральный механизм взаимодействия модулей. Все модули подписываются на
интересующие их типы событий и реагируют асинхронно. Обеспечивает слабую
связанность между компонентами системы.

События (из ТЗ раздел 2):
    TRIGGER_FIRED          — цена БА достигла триггерного уровня
    TRIGGER_DEACTIVATED    — цена БА ушла за порог деактивации
    ORDER_PLACED           — лимитный ордер выставлен в стакан
    ORDER_FILLED           — ордер полностью исполнен
    ORDER_PARTIAL_FILL     — ордер частично исполнен
    ORDER_CANCELLED        — ордер отменён
    POSITION_UPDATED       — изменение позиции (открытие/закрытие/изменение объёма)
    HEDGE_REQUIRED         — требуется корректировка хедж-позиции
    HEDGE_EXECUTED         — хедж-сделка исполнена
    STOP_LOSS_TRIGGERED    — сработал стоп-лосс
    TAKE_PROFIT_TRIGGERED  — сработал тейк-профит
    POSITION_CLOSED        — позиция полностью закрыта
    MAX_CONTRACTS_WARNING  — предупреждение о достижении лимита контрактов
    STRATEGY_STOPPED       — стратегия остановлена
    CONNECTION_ERROR       — ошибка подключения к API
    CONNECTION_RESTORED    — подключение восстановлено
    CRITICAL_ERROR         — критическая ошибка

Использование:
    bus = EventBus()

    # Подписка на событие
    async def on_trigger_fired(event):
        print(f"Триггер: {event.data}")

    bus.subscribe(EventType.TRIGGER_FIRED, on_trigger_fired, priority=10)

    # Публикация события
    await bus.publish(EventType.TRIGGER_FIRED, {"strategy_id": 1, "price": 100.5})
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class EventType(Enum):
    """Типы событий в системе."""
    TRIGGER_FIRED = auto()
    TRIGGER_DEACTIVATED = auto()
    ORDER_PLACED = auto()
    ORDER_FILLED = auto()
    ORDER_PARTIAL_FILL = auto()
    ORDER_CANCELLED = auto()
    POSITION_UPDATED = auto()
    HEDGE_REQUIRED = auto()
    HEDGE_EXECUTED = auto()
    STOP_LOSS_TRIGGERED = auto()
    TAKE_PROFIT_TRIGGERED = auto()
    POSITION_CLOSED = auto()
    MAX_CONTRACTS_WARNING = auto()
    STRATEGY_STOPPED = auto()
    CONNECTION_ERROR = auto()
    CONNECTION_RESTORED = auto()
    CRITICAL_ERROR = auto()
    # Системные события (не из ТЗ, для внутренних нужд)
    PROVIDER_CONNECTED = auto()
    PROVIDER_DISCONNECTED = auto()
    QUOTE_UPDATED = auto()
    APP_SHUTDOWN = auto()


@dataclass
class Event:
    """
    Объект события, передаваемый подписчикам.

    Attributes:
        type: Тип события (EventType).
        data: Произвольные данные, ассоциированные с событием.
        timestamp: Время возникновения события (float, time.time()).
        source: Имя модуля-источника (опционально).
    """
    type: EventType
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    source: str = ""


class EventBus:
    """
    Асинхронная шина событий.

    Поддерживает:
        - Подписку на события с приоритетами (меньше число = выше приоритет)
        - Отписку от событий
        - Асинхронную публикацию (параллельный вызов всех обработчиков)
        - Логирование всех событий
        - Синхронную публикацию для использования из GUI-потока (publish_sync)
    """

    def __init__(self):
        # Словарь: EventType → список кортежей (приоритет, id_подписчика, обработчик)
        self._subscribers: Dict[EventType, List[tuple]] = {}
        # Счётчик для генерации уникальных id подписчиков
        self._subscriber_counter: int = 0
        # Множество для отслеживания «горячих» событий (в процессе обработки)
        self._processing_events: Set[int] = set()
        # Блокировка для потокобезопасности
        self._lock = asyncio.Lock()
        logger.info("EventBus инициализирован")

    def subscribe(
        self,
        event_type: EventType,
        handler: Callable[[Event], Coroutine[Any, Any, None]],
        priority: int = 50,
    ) -> int:
        """
        Подписаться на событие.

        Args:
            event_type: Тип события, на которое подписываемся.
            handler: Асинхронная функция-обработчик. Принимает Event.
            priority: Приоритет обработчика (0 = наивысший, 100 = низший).
                      Обработчики с более высоким приоритетом вызываются первыми.

        Returns:
            Уникальный идентификатор подписки (для отписки).
        """
        self._subscriber_counter += 1
        sub_id = self._subscriber_counter

        if event_type not in self._subscribers:
            self._subscribers[event_type] = []

        # Вставляем с сортировкой по приоритету (в порядке возрастания)
        sub_list = self._subscribers[event_type]
        entry = (priority, sub_id, handler)

        # Находим позицию для вставки (бинарный поиск по первому элементу кортежа)
        insert_idx = 0
        for i, (p, _, _) in enumerate(sub_list):
            if p > priority:
                insert_idx = i
                break
        else:
            insert_idx = len(sub_list)

        sub_list.insert(insert_idx, entry)

        logger.debug(
            "Подписка на %s: id=%d, priority=%d, handler=%s",
            event_type.name, sub_id, priority, handler.__name__,
        )
        return sub_id

    def unsubscribe(self, event_type: EventType, sub_id: int) -> bool:
        """
        Отписаться от события.

        Args:
            event_type: Тип события.
            sub_id: Идентификатор подписки (возвращённый subscribe).

        Returns:
            True, если подписка найдена и удалена, иначе False.
        """
        if event_type not in self._subscribers:
            return False

        sub_list = self._subscribers[event_type]
        original_len = len(sub_list)

        self._subscribers[event_type] = [
            entry for entry in sub_list if entry[1] != sub_id
        ]

        removed = len(self._subscribers[event_type]) < original_len
        if removed:
            logger.debug("Отписка от %s: id=%d", event_type.name, sub_id)
        return removed

    async def publish(self, event_type: EventType, data: Dict[str, Any] = None,
                      source: str = "") -> None:
        """
        Опубликовать событие (асинхронно).

        Все подписчики вызываются параллельно через asyncio.gather.
        Исключения в обработчиках логируются, но не прерывают цепочку.

        Args:
            event_type: Тип события.
            data: Данные события (словарь).
            source: Имя модуля-источника.
        """
        if data is None:
            data = {}

        event = Event(type=event_type, data=data, source=source)

        if event_type not in self._subscribers:
            logger.debug("Событие %s: нет подписчиков", event_type.name)
            return

        subscribers = self._subscribers[event_type]
        logger.debug(
            "Событие %s: %d подписчиков, данные=%s",
            event_type.name, len(subscribers), data,
        )

        # Создаём задачи для параллельного вызова всех обработчиков
        tasks = []
        for priority, sub_id, handler in subscribers:
            task = asyncio.create_task(
                self._invoke_handler(handler, event, sub_id)
            )
            tasks.append(task)

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def publish_sync(self, event_type: EventType, data: Dict[str, Any] = None,
                     source: str = "") -> None:
        """
        Опубликовать событие синхронно (для использования из GUI-потока PyQt).

        Создаёт asyncio.Task в текущем event loop без ожидания завершения.
        Безопасно для вызова из синхронного кода (например, слоты PyQt).

        Args:
            event_type: Тип события.
            data: Данные события.
            source: Имя модуля-источника.
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.publish(event_type, data, source))
        except RuntimeError:
            logger.warning("Не удалось получить event loop для publish_sync")

    async def _invoke_handler(
        self,
        handler: Callable[[Event], Coroutine[Any, Any, None]],
        event: Event,
        sub_id: int,
    ) -> None:
        """
        Вызвать один обработчик с перехватом исключений.

        Args:
            handler: Асинхронная функция-обработчик.
            event: Объект события.
            sub_id: ID подписчика (для логирования ошибок).
        """
        try:
            await handler(event)
        except Exception as exc:
            logger.error(
                "Ошибка в обработчике события %s (sub_id=%d, handler=%s): %s",
                event.type.name, sub_id, handler.__name__, exc,
                exc_info=True,
            )

    def subscriber_count(self, event_type: EventType) -> int:
        """Количество подписчиков на событие указанного типа."""
        return len(self._subscribers.get(event_type, []))

    def total_subscribers(self) -> int:
        """Общее количество подписок (все события, все обработчики)."""
        return sum(len(subs) for subs in self._subscribers.values())
