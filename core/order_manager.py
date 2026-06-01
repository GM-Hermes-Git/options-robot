"""
Управление ордерами (OrderManager).

Отвечает за выставление, переставление, отмену лимитных ордеров.
Контролирует очерёдность исполнения ног стратегии и порционный набор позиции.

Логика (из ТЗ, раздел 5.2):
    - Все ордера — исключительно лимитные
    - Цена ордера рассчитывается через теор. цену (Блэк-76)
    - Ноги исполняются последовательно согласно leg_index
    - Набор порциями с контролем max_contracts_per_leg
    - При изменении IV ордер переставляется (modify_order)
"""

import logging
from typing import Any, Dict, List, Optional

from core.event_bus import EventBus, EventType, Event
from core.providers.market_data import (
    OrderProvider,
    MarketDataProvider,
    OrderRequest,
    OrderInfo,
    OrderSide,
    OrderStatus,
    OptionType,
    Quote,
)
from core.strategy_manager import StrategyDefinition, Leg
from core.greeks_engine import GreeksEngine

logger = logging.getLogger(__name__)


class OrderManager:
    """
    Менеджер ордеров — полная реализация для Этапа 2.

    Подписывается на события шины и управляет лимитными ордерами стратегий.

    Attributes:
        _event_bus: Шина событий.
        _order_provider: Провайдер выставления/отмены ордеров.
        _data_provider: Провайдер рыночных данных.
        _greeks_engine: Движок расчёта теоретической цены.
        _iv_change_threshold: Порог изменения IV для переставления ордера.
        _active_orders: Состояние активных ордеров по стратегиям.
        _default_time_to_expiry: Время до экспирации (константа для Этапа 2).
    """

    def __init__(
        self,
        event_bus: EventBus,
        order_provider: OrderProvider,
        data_provider: MarketDataProvider,
        greeks_engine: GreeksEngine,
        config: dict,
    ):
        """
        Инициализация OrderManager.

        Args:
            event_bus: Шина событий для подписки и публикации.
            order_provider: Провайдер ордеров (OrderProvider).
            data_provider: Провайдер рыночных данных (MarketDataProvider).
            greeks_engine: Движок расчёта греков (GreeksEngine).
            config: Конфигурация приложения.
        """
        self._event_bus = event_bus
        self._order_provider = order_provider
        self._data_provider = data_provider
        self._greeks_engine = greeks_engine
        self._config = config

        # Порог изменения IV для переставления ордера (по умолчанию 0.01 = 1%)
        om_config = config.get("order_manager", {})
        self._iv_change_threshold: float = om_config.get(
            "iv_change_threshold", 0.01
        )

        # Состояние активных ордеров: {strategy_id: state_dict}
        # state_dict = {
        #     "strategy": StrategyDefinition,
        #     "current_leg_index": int,
        #     "filled_quantity": int,
        #     "active_order": OrderInfo | None,
        #     "active_order_iv": float | None,
        # }
        self._active_orders: Dict[int, Dict[str, Any]] = {}

        # Время до экспирации (константа для Этапа 2, позже — из доски опционов)
        self._default_time_to_expiry: float = 0.25  # 3 месяца

        # Подписка на события
        self._subscriptions: List[int] = []
        self._subscriptions.append(
            self._event_bus.subscribe(
                EventType.TRIGGER_FIRED,
                self._on_trigger_fired,
                priority=10,
            )
        )
        self._subscriptions.append(
            self._event_bus.subscribe(
                EventType.TRIGGER_DEACTIVATED,
                self._on_trigger_deactivated,
                priority=10,
            )
        )
        self._subscriptions.append(
            self._event_bus.subscribe(
                EventType.ORDER_FILLED,
                self._on_order_filled,
                priority=10,
            )
        )
        self._subscriptions.append(
            self._event_bus.subscribe(
                EventType.ORDER_PARTIAL_FILL,
                self._on_order_partial_fill,
                priority=10,
            )
        )
        self._subscriptions.append(
            self._event_bus.subscribe(
                EventType.ORDER_CANCELLED,
                self._on_order_cancelled,
                priority=10,
            )
        )
        self._subscriptions.append(
            self._event_bus.subscribe(
                EventType.QUOTE_UPDATED,
                self._on_quote_updated,
                priority=30,
            )
        )

        logger.info(
            "OrderManager инициализирован: iv_threshold=%.2f%%",
            self._iv_change_threshold * 100,
        )

    # ─────────────────────────────────────────────────────────────────
    # Обработчики событий
    # ─────────────────────────────────────────────────────────────────

    async def _on_trigger_fired(self, event: Event) -> None:
        """
        Обработчик TRIGGER_FIRED.

        Извлекает стратегию из event.data, инициализирует состояние
        и запускает выставление первой ноги.

        Args:
            event: Событие с данными {strategy_id, strategy, price}.
        """
        strategy_id = event.data.get("strategy_id")
        strategy = event.data.get("strategy")

        if strategy_id is None or strategy is None:
            logger.warning("TRIGGER_FIRED: нет strategy_id или strategy в данных")
            return

        # Если стратегия уже активна — игнорируем повторный триггер
        if strategy_id in self._active_orders:
            logger.debug(
                "Стратегия #%d уже активна в OrderManager, пропускаем",
                strategy_id,
            )
            return

        # Инициализируем состояние
        self._active_orders[strategy_id] = {
            "strategy": strategy,
            "current_leg_index": 0,
            "filled_quantity": 0,
            "active_order": None,
            "active_order_iv": None,
        }

        logger.info(
            "Стратегия #%d активирована: начинаем набор ноги 0",
            strategy_id,
        )

        # Выставляем первый ордер (нога 0)
        await self._place_next_leg(strategy_id)

    async def _on_trigger_deactivated(self, event: Event) -> None:
        """
        Обработчик TRIGGER_DEACTIVATED.

        Отменяет активный ордер (если есть) и очищает состояние стратегии.
        Уже исполненные контракты не отменяются.

        Args:
            event: Событие с данными {strategy_id}.
        """
        strategy_id = event.data.get("strategy_id")
        if strategy_id is None:
            return

        state = self._active_orders.pop(strategy_id, None)
        if state is None:
            logger.debug("TRIGGER_DEACTIVATED: стратегия #%d не найдена", strategy_id)
            return

        # Отменяем активный ордер, если есть
        if state.get("active_order") is not None:
            order = state["active_order"]
            logger.info(
                "Деактивация стратегии #%d: отмена ордера %s",
                strategy_id,
                order.order_id,
            )
            await self._order_provider.cancel_order(order.order_id)

        logger.info(
            "Стратегия #%d деактивирована (исполнено %d контрактов)",
            strategy_id,
            state.get("filled_quantity", 0),
        )

    async def _on_order_filled(self, event: Event) -> None:
        """
        Обработчик ORDER_FILLED (полное исполнение ордера).

        Увеличивает filled_quantity. Если текущая нога полностью исполнена —
        переходит к следующей. Если не полностью — выставляет следующий
        порционный ордер для той же ноги.

        Args:
            event: Событие с данными {strategy_id, leg_index, filled_quantity, ...}.
        """
        strategy_id = event.data.get("strategy_id")
        if strategy_id is None:
            return

        state = self._active_orders.get(strategy_id)
        if state is None:
            return

        filled_qty = event.data.get("filled_quantity", 0)
        state["filled_quantity"] += filled_qty

        # Сбрасываем активный ордер — он исполнен
        state["active_order"] = None
        state["active_order_iv"] = None

        strategy: StrategyDefinition = state["strategy"]
        leg_index = state["current_leg_index"]

        # Проверяем, все ли ноги исполнены
        if leg_index >= len(strategy.legs):
            logger.info("Стратегия #%d: все ноги исполнены", strategy_id)
            return

        current_leg = strategy.legs[leg_index]

        # Если нога полностью исполнена — переходим к следующей
        if state["filled_quantity"] >= current_leg.quantity:
            logger.info(
                "Стратегия #%d: нога %d полностью исполнена (%d/%d)",
                strategy_id,
                leg_index,
                state["filled_quantity"],
                current_leg.quantity,
            )
            state["current_leg_index"] += 1
            state["filled_quantity"] = 0

            # Если есть следующая нога — начинаем её
            await self._place_next_leg(strategy_id)
        else:
            # Нога исполнена частично — выставляем следующий порционный ордер
            logger.info(
                "Стратегия #%d: нога %d исполнена частично (%d/%d), "
                "выставляем следующий ордер",
                strategy_id,
                leg_index,
                state["filled_quantity"],
                current_leg.quantity,
            )
            await self._place_next_leg(strategy_id)

    async def _on_order_partial_fill(self, event: Event) -> None:
        """
        Обработчик ORDER_PARTIAL_FILL (частичное исполнение).

        Обновляет filled_quantity. Новый ордер не выставляется —
        ждём полного исполнения текущего.

        Args:
            event: Событие с данными {strategy_id, leg_index, filled_quantity, ...}.
        """
        strategy_id = event.data.get("strategy_id")
        if strategy_id is None:
            return

        state = self._active_orders.get(strategy_id)
        if state is None:
            return

        filled_qty = event.data.get("filled_quantity", 0)
        state["filled_quantity"] += filled_qty

        logger.debug(
            "Стратегия #%d: частичное исполнение +%d, всего %d",
            strategy_id,
            filled_qty,
            state["filled_quantity"],
        )

    async def _on_order_cancelled(self, event: Event) -> None:
        """
        Обработчик ORDER_CANCELLED.

        Если ордер был отменён не по нашей инициативе (внешняя отмена) —
        перевыставляем его.

        Args:
            event: Событие с данными {strategy_id, leg_index, order_id, ...}.
        """
        strategy_id = event.data.get("strategy_id")
        if strategy_id is None:
            return

        state = self._active_orders.get(strategy_id)
        if state is None:
            return

        # Если активный ордер отсутствует — ничего не делаем
        if state.get("active_order") is None:
            return

        order_id = event.data.get("order_id")
        active_order_id = state["active_order"].order_id

        # Проверяем, что отменён именно наш активный ордер
        if order_id is not None and order_id != active_order_id:
            return

        logger.info(
            "Стратегия #%d: ордер %s отменён — перевыставляем",
            strategy_id,
            order_id,
        )

        # Сбрасываем и перевыставляем
        state["active_order"] = None
        state["active_order_iv"] = None
        await self._place_next_leg(strategy_id)

    async def _on_quote_updated(self, event: Event) -> None:
        """
        Обработчик QUOTE_UPDATED.

        Проверяет изменение IV для активных ордеров. Если IV изменилась
        существенно — пересчитывает цену и переставляет ордер.

        Args:
            event: Событие с данными {instrument, quote}.
        """
        instrument = event.data.get("instrument")
        quote: Optional[Quote] = event.data.get("quote")

        if not instrument or not quote:
            return

        # Получаем IV из котировки (implied_volatility или расчётная)
        new_iv = None
        if quote.implied_volatility is not None:
            new_iv = quote.implied_volatility

        if new_iv is None:
            return

        # Ищем активные ордера, совпадающие по инструменту
        for strategy_id, state in list(self._active_orders.items()):
            active_order = state.get("active_order")
            if active_order is None:
                continue

            if active_order.instrument != instrument:
                continue

            saved_iv = state.get("active_order_iv")
            if saved_iv is None:
                continue

            iv_change = abs(new_iv - saved_iv)
            if iv_change <= self._iv_change_threshold:
                logger.debug(
                    "Стратегия #%d: IV изменилась на %.4f (порог %.4f) — "
                    "без изменений",
                    strategy_id,
                    iv_change,
                    self._iv_change_threshold,
                )
                continue

            # IV изменилась существенно — пересчитываем цену
            logger.info(
                "Стратегия #%d: IV изменилась с %.4f на %.4f — "
                "переставляем ордер %s",
                strategy_id,
                saved_iv,
                new_iv,
                active_order.order_id,
            )

            strategy: StrategyDefinition = state["strategy"]
            leg_index = state["current_leg_index"]

            if leg_index >= len(strategy.legs):
                continue

            leg = strategy.legs[leg_index]

            # Пересчитываем цену с новой IV
            new_price = await self._calculate_order_price_for_iv(
                strategy, leg, new_iv
            )

            # Меняем ордер
            modified = await self._order_provider.modify_order(
                active_order.order_id,
                new_price,
                active_order.quantity,
            )

            if modified is not None:
                state["active_order"] = modified
                state["active_order_iv"] = new_iv

    # ─────────────────────────────────────────────────────────────────
    # Внутренние методы управления ордерами
    # ─────────────────────────────────────────────────────────────────

    async def _place_next_leg(self, strategy_id: int) -> None:
        """
        Выставить следующий ордер для стратегии.

        Определяет текущую ногу, количество контрактов (с учётом порций),
        рассчитывает лимитную цену и выставляет ордер.

        Args:
            strategy_id: ID стратегии.
        """
        state = self._active_orders.get(strategy_id)
        if state is None:
            logger.warning("_place_next_leg: стратегия #%d не найдена", strategy_id)
            return

        strategy: StrategyDefinition = state["strategy"]
        leg_index = state["current_leg_index"]

        # Проверяем, что есть ещё ноги для исполнения
        if leg_index >= len(strategy.legs):
            logger.info(
                "Стратегия #%d: все ноги исполнены, новых ордеров не будет",
                strategy_id,
            )
            return

        leg = strategy.legs[leg_index]

        # Расчёт количества контрактов в порции
        remaining = leg.quantity - state["filled_quantity"]
        if remaining <= 0:
            # Эта нога уже полностью исполнена, переходим к следующей
            state["current_leg_index"] += 1
            state["filled_quantity"] = 0
            await self._place_next_leg(strategy_id)
            return

        order_quantity = min(remaining, strategy.max_contracts_per_leg)

        # Расчёт лимитной цены
        price = await self._calculate_order_price(strategy, leg)

        # Формируем инструмент (упрощённо: base_asset + страйк + тип)
        instrument = self._make_instrument(strategy, leg)

        # Определяем сторону ордера
        side = OrderSide.BUY if leg.sign == 1 else OrderSide.SELL

        # Выставляем ордер
        request = OrderRequest(
            instrument=instrument,
            side=side,
            quantity=order_quantity,
            price=price,
            comment=f"strategy={strategy_id},leg={leg_index}",
            client_order_id=f"strat_{strategy_id}_leg{leg_index}",
        )

        order = await self._order_provider.place_order(request)
        if order is None:
            logger.error(
                "Стратегия #%d: не удалось выставить ордер для ноги %d",
                strategy_id,
                leg_index,
            )
            return

        # Сохраняем состояние
        state["active_order"] = order
        state["active_order_iv"] = self._get_iv_for_leg(leg)

        logger.info(
            "Стратегия #%d: выставлен ордер %s: %s %s %d лот(а) по %.2f "
            "(нога %d, IV=%.4f)",
            strategy_id,
            order.order_id,
            side.value,
            instrument,
            order_quantity,
            price,
            leg_index,
            state["active_order_iv"],
        )

    async def _calculate_order_price(
        self, strategy: StrategyDefinition, leg: Leg
    ) -> float:
        """
        Рассчитать лимитную цену ордера через модель Блэка-76.

        Args:
            strategy: Определение стратегии.
            leg: Нога стратегии.

        Returns:
            Лимитная цена ордера.
        """
        # Получаем текущую цену фьючерса
        futures_price = await self._data_provider.get_futures_price(
            strategy.base_asset
        )
        if futures_price is None or futures_price <= 0:
            logger.warning(
                "Стратегия #%d: цена фьючерса недоступна, используем 0",
                strategy.strategy_id,
            )
            return 0.0

        # Получаем IV
        iv = self._get_iv_for_leg(leg)

        # Применяем множитель
        adjusted_iv = iv * leg.iv_multiplier

        # Время до экспирации (константа для Этапа 2)
        time_to_expiry = self._default_time_to_expiry

        # Рассчитываем теоретическую цену
        result = self._greeks_engine.calculate(
            futures_price=futures_price,
            strike=leg.strike,
            time_to_expiry=time_to_expiry,
            volatility=adjusted_iv,
            option_type=leg.option_type,
        )

        return result.price

    async def _calculate_order_price_for_iv(
        self, strategy: StrategyDefinition, leg: Leg, iv: float
    ) -> float:
        """
        Рассчитать лимитную цену ордера с заданной IV (для переставления).

        Args:
            strategy: Определение стратегии.
            leg: Нога стратегии.
            iv: Новая волатильность.

        Returns:
            Лимитная цена ордера.
        """
        futures_price = await self._data_provider.get_futures_price(
            strategy.base_asset
        )
        if futures_price is None or futures_price <= 0:
            return 0.0

        adjusted_iv = iv * leg.iv_multiplier
        time_to_expiry = self._default_time_to_expiry

        result = self._greeks_engine.calculate(
            futures_price=futures_price,
            strike=leg.strike,
            time_to_expiry=time_to_expiry,
            volatility=adjusted_iv,
            option_type=leg.option_type,
        )

        return result.price

    # ─────────────────────────────────────────────────────────────────
    # Вспомогательные методы
    # ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _make_instrument(strategy: StrategyDefinition, leg: Leg) -> str:
        """
        Сформировать тикер инструмента для ордера.

        Упрощённый формат: {base_asset}-{strike}{C/P}

        Args:
            strategy: Определение стратегии.
            leg: Нога стратегии.

        Returns:
            Тикер инструмента.
        """
        suffix = "C" if leg.option_type == OptionType.CALL else "P"
        return f"{strategy.base_asset}-{leg.strike:.0f}{suffix}"

    @staticmethod
    def _get_iv_for_leg(leg: Leg) -> float:
        """
        Получить волатильность (IV) для ноги.

        Args:
            leg: Нога стратегии.

        Returns:
            Волатильность в десятичных долях.
        """
        if leg.iv_mode == "manual" and leg.manual_iv is not None:
            return leg.manual_iv
        # Для рыночного режима — возвращаем дефолт (будет заменено на
        # расчёт из котировок на следующих этапах)
        return 0.30

    async def get_active_strategies(self) -> Dict[int, Dict[str, Any]]:
        """
        Получить состояния активных стратегий.

        Returns:
            Словарь {strategy_id: state}.
        """
        return dict(self._active_orders)

    async def is_strategy_active(self, strategy_id: int) -> bool:
        """
        Проверить, активна ли стратегия в OrderManager.

        Args:
            strategy_id: ID стратегии.

        Returns:
            True, если стратегия управляется OrderManager.
        """
        return strategy_id in self._active_orders
