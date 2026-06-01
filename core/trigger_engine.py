"""
Логика триггеров (TriggerEngine).

Мониторит цену базового актива (БА) и при достижении заданного уровня
активирует стратегию. Реализует механизм деактивации триггера при уходе
цены за порог.

Логика (из ТЗ, раздел 5.1):
    - Триггер активируется при касании ценой БА заданного уровня
    - После активации OrderManager начинает выставлять ордера
    - Если цена БА уходит от уровня на величину порога — ордера снимаются
    - Уже исполненные контракты при деактивации не отменяются
"""

import asyncio
import logging
from typing import Dict, Optional

from core.event_bus import EventBus, EventType
from core.providers.market_data import MarketDataProvider
from core.strategy_manager import StrategyDefinition

logger = logging.getLogger(__name__)


class TriggerEngine:
    """
    Движок триггеров — мониторинг цены БА и активация/деактивация.

    Атрибуты:
        _event_bus: Шина событий для публикации TRIGGER_FIRED / TRIGGER_DEACTIVATED.
        _data_provider: Провайдер рыночных данных (MarketDataProvider).
        _polling_interval: Интервал опроса цены в секундах.
        _active_strategies: Словарь активных стратегий с состоянием триггера.
        _polling_task: Фоновая задача цикла опроса.
        _running: Флаг работы движка.
    """

    def __init__(self, event_bus: EventBus, data_provider: MarketDataProvider, config: dict):
        """
        Инициализация TriggerEngine.

        Args:
            event_bus: Шина событий.
            data_provider: Провайдер рыночных данных (реализация MarketDataProvider).
            config: Конфигурация (ключи: strategies.polling_interval_sec,
                    strategies.trigger_deactivation_default).
        """
        self._event_bus = event_bus
        self._data_provider = data_provider

        strategies_config = config.get("strategies", {})
        self._polling_interval = strategies_config.get(
            "polling_interval_sec", 1.0
        )
        self._deactivation_default = strategies_config.get(
            "trigger_deactivation_default", 100
        )

        # Словарь активных стратегий:
        # ключ: strategy_id
        # значение: {"strategy": StrategyDefinition, "trigger_fired": bool, "last_price": float | None}
        self._active_strategies: Dict[int, dict] = {}

        # Фоновая задача цикла опроса
        self._polling_task: Optional[asyncio.Task] = None
        self._running: bool = False

        logger.info(
            "TriggerEngine инициализирован (polling_interval=%.3fs, deactivation_default=%d)",
            self._polling_interval,
            self._deactivation_default,
        )

    async def start_monitoring(self, strategy: StrategyDefinition) -> None:
        """
        Добавить стратегию в мониторинг.

        Если фоновая задача поллинга не запущена — запускает её.

        Args:
            strategy: Объект стратегии (StrategyDefinition).
        """
        if strategy.strategy_id in self._active_strategies:
            logger.debug(
                "Стратегия #%d уже в мониторинге, пропускаем",
                strategy.strategy_id,
            )
            return

        self._active_strategies[strategy.strategy_id] = {
            "strategy": strategy,
            "trigger_fired": False,
            "last_price": None,
        }

        logger.info(
            "Стратегия #%d '%s' добавлена в мониторинг (БА=%s, уровень=%.2f, порог=%.2f)",
            strategy.strategy_id,
            strategy.name,
            strategy.base_asset,
            strategy.trigger_level,
            strategy.trigger_deactivation_threshold or self._deactivation_default,
        )

        if self._polling_task is None or self._polling_task.done():
            self._running = True
            self._polling_task = asyncio.create_task(self._polling_loop())
            logger.debug("Запущен фоновый цикл опроса цен")

    async def stop_monitoring(self, strategy_id: int) -> None:
        """
        Убрать стратегию из мониторинга.

        Если активных стратегий не осталось — останавливает фоновый цикл опроса.

        Args:
            strategy_id: ID стратегии.
        """
        if strategy_id in self._active_strategies:
            del self._active_strategies[strategy_id]
            logger.info(
                "Стратегия #%d удалена из мониторинга", strategy_id
            )

        if not self._active_strategies and self._polling_task is not None:
            await self._stop_polling()

    async def _polling_loop(self) -> None:
        """
        Бесконечный цикл опроса цен базовых активов.

        Для каждой активной стратегии запрашивает цену БА и проверяет триггер.
        """
        logger.debug("Цикл опроса цен запущен")
        while self._running:
            try:
                for strategy_id, state in list(self._active_strategies.items()):
                    strategy: StrategyDefinition = state["strategy"]
                    current_price = await self._data_provider.get_futures_price(
                        strategy.base_asset
                    )
                    if current_price is not None:
                        state["last_price"] = current_price
                        await self._check_trigger(strategy_id, current_price)

                await asyncio.sleep(self._polling_interval)
            except asyncio.CancelledError:
                logger.debug("Цикл опроса цен отменён")
                break
            except Exception as exc:
                logger.error(
                    "Ошибка в цикле опроса цен: %s", exc, exc_info=True
                )
                await asyncio.sleep(self._polling_interval)

        logger.debug("Цикл опроса цен завершён")

    async def _check_trigger(
        self, strategy_id: int, current_price: float
    ) -> None:
        """
        Проверить состояние триггера для стратегии и при необходимости
        опубликовать событие.

        Логика:
            a) Если триггер ещё не срабатывал (trigger_fired=False):
               - Если current_price >= trigger_level:
                 → trigger_fired = True
                 → публикует TRIGGER_FIRED

            b) Если триггер уже срабатывал (trigger_fired=True):
               - Если |current_price - trigger_level| > trigger_deactivation_threshold:
                 → trigger_fired = False
                 → публикует TRIGGER_DEACTIVATED

        Args:
            strategy_id: ID стратегии.
            current_price: Текущая цена базового актива.
        """
        state = self._active_strategies.get(strategy_id)
        if state is None:
            return

        strategy: StrategyDefinition = state["strategy"]
        trigger_level = strategy.trigger_level
        threshold = strategy.trigger_deactivation_threshold or self._deactivation_default

        if not state["trigger_fired"]:
            # Проверка активации: цена достигла или превысила уровень
            if current_price >= trigger_level:
                state["trigger_fired"] = True
                logger.info(
                    "Триггер стратегии #%d сработал (уровень=%.2f, цена=%.2f)",
                    strategy_id,
                    trigger_level,
                    current_price,
                )
                await self._event_bus.publish(
                    EventType.TRIGGER_FIRED,
                    {"strategy_id": strategy_id, "price": current_price},
                    source="trigger_engine",
                )
        else:
            # Проверка деактивации: цена ушла за порог
            if abs(current_price - trigger_level) > threshold:
                state["trigger_fired"] = False
                logger.info(
                    "Триггер стратегии #%d деактивирован (уровень=%.2f, порог=%.2f, цена=%.2f)",
                    strategy_id,
                    trigger_level,
                    threshold,
                    current_price,
                )
                await self._event_bus.publish(
                    EventType.TRIGGER_DEACTIVATED,
                    {"strategy_id": strategy_id, "price": current_price},
                    source="trigger_engine",
                )

    async def _stop_polling(self) -> None:
        """Остановить фоновый цикл опроса и дождаться его завершения."""
        self._running = False
        if self._polling_task is not None and not self._polling_task.done():
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
        self._polling_task = None
        logger.debug("Цикл опроса цен остановлен")

    async def stop(self) -> None:
        """
        Полная остановка TriggerEngine.

        - Останавливает цикл опроса
        - Очищает словарь активных стратегий
        """
        await self._stop_polling()
        self._active_strategies.clear()
        logger.info("TriggerEngine остановлен")

    def is_monitoring(self, strategy_id: int) -> bool:
        """
        Проверить, мониторится ли стратегия.

        Args:
            strategy_id: ID стратегии.

        Returns:
            True, если стратегия в мониторинге.
        """
        return strategy_id in self._active_strategies

    def active_count(self) -> int:
        """
        Количество стратегий в мониторинге.

        Returns:
            Число активных стратегий.
        """
        return len(self._active_strategies)
