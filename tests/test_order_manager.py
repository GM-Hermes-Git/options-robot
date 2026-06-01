"""
Тесты OrderManager (TDD).

Проверяют:
- Выставление лимитных ордеров при TRIGGER_FIRED
- Последовательное исполнение ног
- Порционный набор (max_contracts_per_leg)
- Переставление при изменении IV
- Отмену ордеров при TRIGGER_DEACTIVATED
- Перевыставление после отмены
- Частичное исполнение
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytest

from core.event_bus import EventBus, EventType, Event
from core.order_manager import OrderManager
from core.providers.market_data import (
    OrderRequest,
    OrderInfo,
    OrderSide,
    OrderStatus,
    OptionType,
    Quote,
)
from core.strategy_manager import StrategyDefinition, StrategyStatus, Leg
from core.greeks_engine import GreeksEngine, Black76Result


# ═══════════════════════════════════════════════════════════════
# Моки
# ═══════════════════════════════════════════════════════════════


class MockOrderProvider:
    """Мок OrderProvider для тестов.

    Attributes:
        placed_orders: История выставленных ордеров.
        cancelled_orders: История отменённых ордеров (order_id).
        modified_orders: История изменений (order_id, new_price, new_quantity).
    """

    def __init__(self):
        self.placed_orders: List[OrderInfo] = []
        self.cancelled_orders: List[str] = []
        self.modified_orders: List[tuple] = []
        self._next_order_id: int = 100

    async def place_order(self, request: OrderRequest) -> Optional[OrderInfo]:
        """Выставить ордер (мок — без симуляции исполнения)."""
        order = OrderInfo(
            order_id=str(self._next_order_id),
            client_order_id=request.client_order_id,
            instrument=request.instrument,
            side=request.side,
            quantity=request.quantity,
            price=request.price,
            status=OrderStatus.ACTIVE,
            comment=request.comment,
            filled_quantity=0,
        )
        self._next_order_id += 1
        self.placed_orders.append(order)
        return order

    async def cancel_order(self, order_id: str) -> bool:
        """Отменить ордер."""
        self.cancelled_orders.append(order_id)
        return True

    async def modify_order(
        self, order_id: str, new_price: float, new_quantity: int
    ) -> Optional[OrderInfo]:
        """Изменить ордер."""
        self.modified_orders.append((order_id, new_price, new_quantity))
        return OrderInfo(
            order_id=str(order_id),
            price=new_price,
            quantity=new_quantity,
            status=OrderStatus.ACTIVE,
        )

    async def get_orders(self) -> List[OrderInfo]:
        return []

    async def get_positions(self) -> list:
        return []

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        pass

    async def is_connected(self) -> bool:
        return True


class MockDataProvider:
    """Мок MarketDataProvider для тестов."""

    def __init__(
        self,
        futures_price: float = 100000.0,
        quote: Optional[Quote] = None,
    ):
        self.futures_price = futures_price
        self._quote = quote

    async def get_futures_price(self, base_asset: str) -> Optional[float]:
        return self.futures_price

    async def get_quote(self, instrument: str) -> Optional[Quote]:
        return self._quote

    def set_quote(self, quote: Quote) -> None:
        """Установить котировку для тестов."""
        self._quote = quote

    def set_futures_price(self, price: float) -> None:
        """Установить цену фьючерса для тестов."""
        self.futures_price = price

    async def get_quotes(self, instruments: list) -> Dict[str, Quote]:
        return {}

    async def get_option_chain(self, base_asset: str, expiration_date=None) -> list:
        return []

    async def subscribe_quotes(self, instruments: list) -> bool:
        return True

    async def unsubscribe_quotes(self, instruments: list) -> None:
        pass

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        pass

    async def is_connected(self) -> bool:
        return True


class MockGreeksEngine:
    """Мок GreeksEngine для тестов, возвращающий предсказуемую цену.

    По умолчанию price = volatility * 10000, что упрощает проверки.
    """

    def __init__(self):
        self.last_call: Optional[dict] = None

    def calculate(
        self,
        futures_price: float,
        strike: float,
        time_to_expiry: float,
        volatility: float,
        option_type: OptionType,
    ) -> Black76Result:
        """Рассчитать цену = volatility * 10000 для простоты тестов."""
        self.last_call = {
            "futures_price": futures_price,
            "strike": strike,
            "time_to_expiry": time_to_expiry,
            "volatility": volatility,
            "option_type": option_type,
        }
        price = volatility * 10000.0
        return Black76Result(
            price=price,
            delta=0.5,
            gamma=0.001,
            theta=-100.0,
            theta_daily=-0.274,
            vega=50.0,
            rho=10.0,
            d1=0.5,
            d2=0.3,
            forward=futures_price,
            strike=strike,
            time_to_expiry=time_to_expiry,
            volatility=volatility,
            risk_free_rate=0.085,
            option_type=option_type,
        )

    def implied_volatility(
        self,
        market_price: float,
        futures_price: float,
        strike: float,
        time_to_expiry: float,
        option_type: OptionType,
        initial_guess: Optional[float] = None,
    ) -> Optional[float]:
        """Мок для implied_volatility — возвращает фиксированное значение."""
        return 0.35


# ═══════════════════════════════════════════════════════════════
# Фикстуры
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def event_bus():
    """Создать чистую шину событий."""
    return EventBus()


@pytest.fixture
def order_provider():
    """Создать мок провайдера ордеров."""
    return MockOrderProvider()


@pytest.fixture
def data_provider():
    """Создать мок провайдера данных."""
    return MockDataProvider(futures_price=100000.0)


@pytest.fixture
def greeks_engine():
    """Создать мок GreeksEngine."""
    return MockGreeksEngine()


@pytest.fixture
def config():
    """Конфигурация по умолчанию."""
    return {
        "order_manager": {
            "iv_change_threshold": 0.01,  # 1%
        }
    }


@pytest.fixture
def manager(event_bus, order_provider, data_provider, greeks_engine, config):
    """Создать OrderManager с моками."""
    return OrderManager(
        event_bus=event_bus,
        order_provider=order_provider,
        data_provider=data_provider,
        greeks_engine=greeks_engine,
        config=config,
    )


# ═══════════════════════════════════════════════════════════════
# Вспомогательные функции для создания тестовых стратегий
# ═══════════════════════════════════════════════════════════════


def make_strategy(
    strategy_id: int = 1,
    legs: Optional[List[Leg]] = None,
    max_contracts_per_leg: int = 10,
    base_asset: str = "Si",
) -> StrategyDefinition:
    """Создать стратегию для тестов с ручной IV."""
    if legs is None:
        legs = [
            Leg(
                leg_index=0,
                option_type=OptionType.CALL,
                strike=95000.0,
                sign=1,
                quantity=5,
                iv_mode="manual",
                manual_iv=0.30,
                iv_multiplier=1.0,
            ),
        ]
    return StrategyDefinition(
        strategy_id=strategy_id,
        name=f"Test Strategy #{strategy_id}",
        base_asset=base_asset,
        status=StrategyStatus.ACTIVE,
        legs=legs,
        trigger_level=100000.0,
        max_contracts_per_leg=max_contracts_per_leg,
    )


def make_leg(
    leg_index: int = 0,
    option_type: OptionType = OptionType.CALL,
    strike: float = 95000.0,
    sign: int = 1,
    quantity: int = 5,
    iv_mode: str = "manual",
    manual_iv: float = 0.30,
    iv_multiplier: float = 1.0,
) -> Leg:
    """Создать ногу для тестов."""
    return Leg(
        leg_index=leg_index,
        option_type=option_type,
        strike=strike,
        sign=sign,
        quantity=quantity,
        iv_mode=iv_mode,
        manual_iv=manual_iv,
        iv_multiplier=iv_multiplier,
    )


async def publish_and_process(event_bus: EventBus, event_type: EventType, data: dict) -> None:
    """Опубликовать событие и дать время на обработку."""
    await event_bus.publish(event_type, data, source="test")
    await asyncio.sleep(0.05)


# ═══════════════════════════════════════════════════════════════
# Тесты
# ═══════════════════════════════════════════════════════════════


class TestTriggerFired:
    """Тесты обработки TRIGGER_FIRED."""

    @pytest.mark.asyncio
    async def test_trigger_fired_starts_placing_orders(
        self, manager, event_bus, order_provider
    ):
        """При TRIGGER_FIRED выставляется первый ордер для ноги 0."""
        strategy = make_strategy(strategy_id=1)
        await publish_and_process(
            event_bus,
            EventType.TRIGGER_FIRED,
            {"strategy_id": 1, "strategy": strategy, "price": 100000.0},
        )
        assert len(order_provider.placed_orders) == 1, (
            "Должен быть выставлен 1 ордер"
        )

    @pytest.mark.asyncio
    async def test_places_order_for_first_leg(
        self, manager, event_bus, order_provider
    ):
        """Ордер выставляется для leg_index=0 с правильными параметрами."""
        strategy = make_strategy(
            strategy_id=2,
            legs=[make_leg(leg_index=0, option_type=OptionType.CALL, quantity=3)],
        )
        await publish_and_process(
            event_bus,
            EventType.TRIGGER_FIRED,
            {"strategy_id": 2, "strategy": strategy, "price": 100000.0},
        )
        order = order_provider.placed_orders[0]
        assert order.quantity == 3, f"Ожидалось quantity=3, получено {order.quantity}"
        assert order.side == OrderSide.BUY, (
            f"Ожидался BUY, получено {order.side}"
        )
        # sign=1 → BUY, sign=-1 → SELL — проверим
        assert "95000" in order.instrument, (
            f"Инструмент должен содержать страйк: {order.instrument}"
        )

    @pytest.mark.asyncio
    async def test_places_order_with_correct_side_for_sell_leg(
        self, manager, event_bus, order_provider
    ):
        """Ордер на продажу (sign=-1) выставляется как SELL."""
        strategy = make_strategy(
            strategy_id=3,
            legs=[make_leg(leg_index=0, sign=-1)],
        )
        await publish_and_process(
            event_bus,
            EventType.TRIGGER_FIRED,
            {"strategy_id": 3, "strategy": strategy, "price": 100000.0},
        )
        order = order_provider.placed_orders[0]
        assert order.side == OrderSide.SELL, (
            f"Ожидался SELL для sign=-1, получено {order.side}"
        )

    @pytest.mark.asyncio
    async def test_event_data_has_correct_strategy_id(
        self, manager, event_bus, order_provider
    ):
        """Корректный strategy_id передаётся в комментарии ордера."""
        strategy = make_strategy(strategy_id=42)
        await publish_and_process(
            event_bus,
            EventType.TRIGGER_FIRED,
            {"strategy_id": 42, "strategy": strategy, "price": 100000.0},
        )
        order = order_provider.placed_orders[0]
        assert "42" in order.comment, (
            f"Комментарий должен содержать strategy_id: {order.comment}"
        )


class TestLimitPriceCalculation:
    """Тесты расчёта лимитной цены."""

    @pytest.mark.asyncio
    async def test_calculates_limit_price_using_black76(
        self, manager, event_bus, order_provider, greeks_engine, data_provider
    ):
        """Цена ордера = теоретическая цена из GreeksEngine."""
        # manual_iv=0.30, iv_multiplier=1.0 → adjusted_iv=0.30
        # MockGreeksEngine возвращает price = volatility * 10000 = 0.30 * 10000 = 3000.0
        strategy = make_strategy(
            strategy_id=10,
            legs=[make_leg(manual_iv=0.30, iv_multiplier=1.0)],
        )
        await publish_and_process(
            event_bus,
            EventType.TRIGGER_FIRED,
            {"strategy_id": 10, "strategy": strategy, "price": 100000.0},
        )
        order = order_provider.placed_orders[0]
        expected_price = 0.30 * 10000.0  # manual_iv * multiplier * 10000
        assert order.price == pytest.approx(expected_price, rel=0.01), (
            f"Ожидалась цена {expected_price}, получена {order.price}"
        )
        # Проверяем, что GreeksEngine вызывался с правильными параметрами
        assert greeks_engine.last_call is not None
        assert greeks_engine.last_call["volatility"] == pytest.approx(0.30)
        assert greeks_engine.last_call["futures_price"] == 100000.0
        assert greeks_engine.last_call["strike"] == 95000.0

    @pytest.mark.asyncio
    async def test_applies_iv_multiplier(
        self, manager, event_bus, order_provider, greeks_engine
    ):
        """iv_multiplier применяется к IV перед расчётом цены."""
        # manual_iv=0.30, iv_multiplier=1.2 → adjusted_iv=0.36
        strategy = make_strategy(
            strategy_id=11,
            legs=[make_leg(manual_iv=0.30, iv_multiplier=1.2)],
        )
        await publish_and_process(
            event_bus,
            EventType.TRIGGER_FIRED,
            {"strategy_id": 11, "strategy": strategy, "price": 100000.0},
        )
        order = order_provider.placed_orders[0]
        expected_price = 0.36 * 10000.0
        assert order.price == pytest.approx(expected_price, rel=0.01), (
            f"Ожидалась цена {expected_price}, получена {order.price}"
        )
        assert greeks_engine.last_call["volatility"] == pytest.approx(0.36)

    @pytest.mark.asyncio
    async def test_uses_futures_price_from_data_provider(
        self, manager, event_bus, order_provider, greeks_engine, data_provider
    ):
        """Цена фьючерса берётся из data_provider."""
        data_provider.set_futures_price(120000.0)
        strategy = make_strategy(
            strategy_id=12,
            legs=[make_leg(manual_iv=0.30)],
        )
        await publish_and_process(
            event_bus,
            EventType.TRIGGER_FIRED,
            {"strategy_id": 12, "strategy": strategy, "price": 120000.0},
        )
        assert greeks_engine.last_call is not None
        assert greeks_engine.last_call["futures_price"] == 120000.0, (
            "Должна использоваться цена из data_provider"
        )


class TestLegSequencing:
    """Тесты последовательного исполнения ног."""

    @pytest.mark.asyncio
    async def test_does_not_start_next_leg_until_current_filled(
        self, manager, event_bus, order_provider
    ):
        """Пока текущая нога не исполнена — следующая не выставляется."""
        legs = [
            make_leg(leg_index=0, quantity=5),
            make_leg(leg_index=1, quantity=3),
        ]
        strategy = make_strategy(strategy_id=20, legs=legs)
        await publish_and_process(
            event_bus,
            EventType.TRIGGER_FIRED,
            {"strategy_id": 20, "strategy": strategy, "price": 100000.0},
        )
        # Должен быть только один ордер — для ноги 0
        assert len(order_provider.placed_orders) == 1, (
            "Не должно быть ордера для ноги 1, пока нога 0 не исполнена"
        )

    @pytest.mark.asyncio
    async def test_advances_to_next_leg_after_fill(
        self, manager, event_bus, order_provider
    ):
        """После полного исполнения ноги 0 выставляется ордер для ноги 1."""
        legs = [
            make_leg(leg_index=0, quantity=3),
            make_leg(leg_index=1, quantity=2),
        ]
        strategy = make_strategy(strategy_id=21, legs=legs)
        await publish_and_process(
            event_bus,
            EventType.TRIGGER_FIRED,
            {"strategy_id": 21, "strategy": strategy, "price": 100000.0},
        )
        assert len(order_provider.placed_orders) == 1

        # Симулируем полное исполнение ордера ноги 0
        await publish_and_process(
            event_bus,
            EventType.ORDER_FILLED,
            {
                "strategy_id": 21,
                "leg_index": 0,
                "order_id": "100",
                "filled_quantity": 3,
                "total_filled": 3,
                "price": 3000.0,
            },
        )
        # Теперь должен появиться ордер для ноги 1
        assert len(order_provider.placed_orders) == 2, (
            "После исполнения ноги 0 должен быть выставлен ордер для ноги 1"
        )

    @pytest.mark.asyncio
    async def test_advances_to_third_leg(
        self, manager, event_bus, order_provider
    ):
        """Последовательное исполнение трёх ног."""
        legs = [
            make_leg(leg_index=0, quantity=1),
            make_leg(leg_index=1, quantity=1),
            make_leg(leg_index=2, quantity=1),
        ]
        strategy = make_strategy(strategy_id=22, legs=legs)
        await publish_and_process(
            event_bus,
            EventType.TRIGGER_FIRED,
            {"strategy_id": 22, "strategy": strategy, "price": 100000.0},
        )
        assert len(order_provider.placed_orders) == 1

        # Исполняем ногу 0
        await publish_and_process(
            event_bus,
            EventType.ORDER_FILLED,
            {"strategy_id": 22, "leg_index": 0, "filled_quantity": 1},
        )
        assert len(order_provider.placed_orders) == 2

        # Исполняем ногу 1
        await publish_and_process(
            event_bus,
            EventType.ORDER_FILLED,
            {"strategy_id": 22, "leg_index": 1, "filled_quantity": 1},
        )
        assert len(order_provider.placed_orders) == 3, (
            "После исполнения ноги 1 должна начаться нога 2"
        )

    @pytest.mark.asyncio
    async def test_does_nothing_when_all_legs_filled(
        self, manager, event_bus, order_provider
    ):
        """Когда все ноги исполнены — новые ордера не выставляются."""
        legs = [
            make_leg(leg_index=0, quantity=1),
            make_leg(leg_index=1, quantity=1),
        ]
        strategy = make_strategy(strategy_id=23, legs=legs)
        await publish_and_process(
            event_bus,
            EventType.TRIGGER_FIRED,
            {"strategy_id": 23, "strategy": strategy, "price": 100000.0},
        )
        # Исполняем ногу 0 → выставляется нога 1
        await publish_and_process(
            event_bus,
            EventType.ORDER_FILLED,
            {"strategy_id": 23, "leg_index": 0, "filled_quantity": 1},
        )
        # Исполняем ногу 1 → все ноги исполнены
        await publish_and_process(
            event_bus,
            EventType.ORDER_FILLED,
            {"strategy_id": 23, "leg_index": 1, "filled_quantity": 1},
        )
        placed_count = len(order_provider.placed_orders)
        # Повторный TRIGGER_FIRED или ORDER_FILLED не должны создавать новые ордера
        # (стратегия уже полностью исполнена)
        await publish_and_process(
            event_bus,
            EventType.ORDER_FILLED,
            {"strategy_id": 23, "leg_index": 2, "filled_quantity": 0},
        )
        assert len(order_provider.placed_orders) == placed_count, (
            "Не должно быть новых ордеров после исполнения всех ног"
        )


class TestPortionControl:
    """Тесты порционного набора (max_contracts_per_leg)."""

    @pytest.mark.asyncio
    async def test_portion_control_max_contracts(
        self, manager, event_bus, order_provider
    ):
        """Количество в ордере = min(quantity, max_contracts_per_leg)."""
        # quantity=10, max_contracts_per_leg=3 → первый ордер на 3
        strategy = make_strategy(
            strategy_id=30,
            legs=[make_leg(leg_index=0, quantity=10)],
            max_contracts_per_leg=3,
        )
        await publish_and_process(
            event_bus,
            EventType.TRIGGER_FIRED,
            {"strategy_id": 30, "strategy": strategy, "price": 100000.0},
        )
        order = order_provider.placed_orders[0]
        assert order.quantity == 3, (
            f"Первый ордер должен быть на 3 контракта, получено {order.quantity}"
        )

    @pytest.mark.asyncio
    async def test_portion_control_subsequent_orders(
        self, manager, event_bus, order_provider
    ):
        """После частичного исполнения следующий ордер на оставшийся лимит."""
        strategy = make_strategy(
            strategy_id=31,
            legs=[make_leg(leg_index=0, quantity=10)],
            max_contracts_per_leg=3,
        )
        await publish_and_process(
            event_bus,
            EventType.TRIGGER_FIRED,
            {"strategy_id": 31, "strategy": strategy, "price": 100000.0},
        )
        assert order_provider.placed_orders[0].quantity == 3

        # Частичное исполнение на 3 контракта
        await publish_and_process(
            event_bus,
            EventType.ORDER_FILLED,
            {
                "strategy_id": 31,
                "leg_index": 0,
                "order_id": "100",
                "filled_quantity": 3,
                "total_filled": 3,
            },
        )
        # Следующий ордер: min(10-3, 3) = min(7, 3) = 3
        assert len(order_provider.placed_orders) == 2
        assert order_provider.placed_orders[1].quantity == 3, (
            f"Второй ордер должен быть на 3 контракта, "
            f"получено {order_provider.placed_orders[1].quantity}"
        )

    @pytest.mark.asyncio
    async def test_portion_control_last_portion(
        self, manager, event_bus, order_provider
    ):
        """Последняя порция — оставшееся количество (может быть меньше лимита)."""
        strategy = make_strategy(
            strategy_id=32,
            legs=[make_leg(leg_index=0, quantity=5)],
            max_contracts_per_leg=3,
        )
        await publish_and_process(
            event_bus,
            EventType.TRIGGER_FIRED,
            {"strategy_id": 32, "strategy": strategy, "price": 100000.0},
        )
        assert order_provider.placed_orders[0].quantity == 3

        # Исполнение первой порции (3)
        await publish_and_process(
            event_bus,
            EventType.ORDER_FILLED,
            {
                "strategy_id": 32,
                "leg_index": 0,
                "order_id": "100",
                "filled_quantity": 3,
                "total_filled": 3,
            },
        )
        # Осталось: min(5-3, 3) = min(2, 3) = 2
        assert len(order_provider.placed_orders) == 2
        assert order_provider.placed_orders[1].quantity == 2, (
            f"Последняя порция должна быть на 2 контракта, "
            f"получено {order_provider.placed_orders[1].quantity}"
        )

    @pytest.mark.asyncio
    async def test_portion_below_max_uses_leg_quantity(
        self, manager, event_bus, order_provider
    ):
        """Если quantity < max_contracts_per_leg — ордер на всё quantity."""
        strategy = make_strategy(
            strategy_id=33,
            legs=[make_leg(leg_index=0, quantity=2)],
            max_contracts_per_leg=10,
        )
        await publish_and_process(
            event_bus,
            EventType.TRIGGER_FIRED,
            {"strategy_id": 33, "strategy": strategy, "price": 100000.0},
        )
        order = order_provider.placed_orders[0]
        assert order.quantity == 2, (
            f"Ордер на все 2 контракта (quantity < max), получено {order.quantity}"
        )


class TestTriggerDeactivated:
    """Тесты обработки TRIGGER_DEACTIVATED."""

    @pytest.mark.asyncio
    async def test_trigger_deactivated_cancels_active_order(
        self, manager, event_bus, order_provider
    ):
        """Деактивация триггера отменяет активный ордер."""
        strategy = make_strategy(strategy_id=40)
        await publish_and_process(
            event_bus,
            EventType.TRIGGER_FIRED,
            {"strategy_id": 40, "strategy": strategy, "price": 100000.0},
        )
        assert len(order_provider.placed_orders) == 1
        order_id = order_provider.placed_orders[0].order_id

        await publish_and_process(
            event_bus,
            EventType.TRIGGER_DEACTIVATED,
            {"strategy_id": 40},
        )
        assert order_id in order_provider.cancelled_orders, (
            f"Ордер {order_id} должен быть отменён"
        )

    @pytest.mark.asyncio
    async def test_trigger_deactivated_keeps_filled(
        self, manager, event_bus, order_provider
    ):
        """Деактивация не отменяет уже исполненные контракты."""
        strategy = make_strategy(
            strategy_id=41,
            legs=[make_leg(leg_index=0, quantity=5)],
        )
        await publish_and_process(
            event_bus,
            EventType.TRIGGER_FIRED,
            {"strategy_id": 41, "strategy": strategy, "price": 100000.0},
        )
        # Частичное исполнение
        await publish_and_process(
            event_bus,
            EventType.ORDER_PARTIAL_FILL,
            {
                "strategy_id": 41,
                "leg_index": 0,
                "order_id": "100",
                "filled_quantity": 2,
                "total_filled": 2,
                "remaining": 3,
            },
        )
        # Деактивация
        await publish_and_process(
            event_bus,
            EventType.TRIGGER_DEACTIVATED,
            {"strategy_id": 41},
        )
        # Проверяем, что не было ошибок — отмена прошла
        assert len(order_provider.cancelled_orders) > 0

    @pytest.mark.asyncio
    async def test_trigger_deactivated_after_full_fill(
        self, manager, event_bus, order_provider
    ):
        """Деактивация после исполнения всех ног — без ошибок."""
        strategy = make_strategy(
            strategy_id=42,
            legs=[make_leg(leg_index=0, quantity=1)],
        )
        await publish_and_process(
            event_bus,
            EventType.TRIGGER_FIRED,
            {"strategy_id": 42, "strategy": strategy, "price": 100000.0},
        )
        await publish_and_process(
            event_bus,
            EventType.ORDER_FILLED,
            {
                "strategy_id": 42,
                "leg_index": 0,
                "order_id": "100",
                "filled_quantity": 1,
                "total_filled": 1,
            },
        )
        # Деактивация после полного исполнения — не должно быть активного ордера
        await publish_and_process(
            event_bus,
            EventType.TRIGGER_DEACTIVATED,
            {"strategy_id": 42},
        )
        # Не должно быть ошибок — отмена без активного ордера игнорируется


class TestIVChange:
    """Тесты переставления ордера при изменении IV."""

    @pytest.mark.asyncio
    async def test_iv_change_triggers_modify_order(
        self, manager, event_bus, order_provider
    ):
        """Изменение IV > порога → ордер переставляется (modify_order)."""
        strategy = make_strategy(
            strategy_id=50,
            legs=[make_leg(
                leg_index=0,
                iv_mode="manual",
                manual_iv=0.30,
                iv_multiplier=1.0,
            )],
        )
        await publish_and_process(
            event_bus,
            EventType.TRIGGER_FIRED,
            {"strategy_id": 50, "strategy": strategy, "price": 100000.0},
        )
        order = order_provider.placed_orders[0]
        # Первый ордер с IV=0.30 → цена = 0.30*10000 = 3000

        # Симулируем QUOTE_UPDATED с новой IV=0.32 (изменение > 0.01)
        # Для ручного режима IV не меняется, поэтому используем
        # механизм: QUOTE_UPDATED содержит quote с implied_volatility
        quote = Quote(
            instrument=order.instrument,
            bid=3200.0,
            ask=3300.0,
            implied_volatility=0.32,
        )
        await publish_and_process(
            event_bus,
            EventType.QUOTE_UPDATED,
            {"instrument": order.instrument, "quote": quote},
        )
        # Должен быть вызван modify_order
        assert len(order_provider.modified_orders) >= 1, (
            "Должен быть переставлен ордер при изменении IV"
        )
        modified = order_provider.modified_orders[0]
        assert modified[0] == order.order_id, (
            f"Должен быть изменён ордер {order.order_id}"
        )
        # Новая цена = 0.32 * 10000 = 3200
        expected_new_price = 0.32 * 10000.0
        assert modified[1] == pytest.approx(expected_new_price, rel=0.01), (
            f"Новая цена должна быть {expected_new_price}, получена {modified[1]}"
        )

    @pytest.mark.asyncio
    async def test_no_modify_within_iv_threshold(
        self, manager, event_bus, order_provider
    ):
        """Малые изменения IV (< порога) игнорируются."""
        strategy = make_strategy(
            strategy_id=51,
            legs=[make_leg(manual_iv=0.30, iv_multiplier=1.0)],
        )
        await publish_and_process(
            event_bus,
            EventType.TRIGGER_FIRED,
            {"strategy_id": 51, "strategy": strategy, "price": 100000.0},
        )
        order = order_provider.placed_orders[0]

        # QUOTE_UPDATED с IV=0.301 — изменение 0.001 < 0.01
        quote = Quote(
            instrument=order.instrument,
            bid=3010.0,
            ask=3020.0,
            implied_volatility=0.301,
        )
        await publish_and_process(
            event_bus,
            EventType.QUOTE_UPDATED,
            {"instrument": order.instrument, "quote": quote},
        )
        assert len(order_provider.modified_orders) == 0, (
            "Малые изменения IV не должны вызывать переставление"
        )

    @pytest.mark.asyncio
    async def test_no_modify_without_active_order(
        self, manager, event_bus, order_provider
    ):
        """QUOTE_UPDATED без активного ордера — без модификации."""
        quote = Quote(
            instrument="Si-95000C",
            bid=3000.0,
            ask=3100.0,
            implied_volatility=0.35,
        )
        await publish_and_process(
            event_bus,
            EventType.QUOTE_UPDATED,
            {"instrument": "Si-95000C", "quote": quote},
        )
        assert len(order_provider.modified_orders) == 0, (
            "Не должно быть модификаций без активных ордеров"
        )


class TestOrderLifecycle:
    """Тесты жизненного цикла ордеров."""

    @pytest.mark.asyncio
    async def test_order_cancelled_replaces_order(
        self, manager, event_bus, order_provider
    ):
        """При отмене ордера (не по нашей инициативе) — перевыставление."""
        strategy = make_strategy(
            strategy_id=60,
            legs=[make_leg(leg_index=0, quantity=5)],
        )
        await publish_and_process(
            event_bus,
            EventType.TRIGGER_FIRED,
            {"strategy_id": 60, "strategy": strategy, "price": 100000.0},
        )
        assert len(order_provider.placed_orders) == 1
        order_id = order_provider.placed_orders[0].order_id

        # Симулируем отмену ордера
        await publish_and_process(
            event_bus,
            EventType.ORDER_CANCELLED,
            {
                "strategy_id": 60,
                "leg_index": 0,
                "order_id": order_id,
                "filled_quantity": 0,
            },
        )
        # Должен быть выставлен новый ордер
        assert len(order_provider.placed_orders) == 2, (
            "После отмены должен быть выставлен новый ордер"
        )

    @pytest.mark.asyncio
    async def test_partial_fill_updates_quantity(
        self, manager, event_bus, order_provider
    ):
        """Частичное исполнение обновляет filled_quantity."""
        strategy = make_strategy(
            strategy_id=61,
            legs=[make_leg(leg_index=0, quantity=5)],
        )
        await publish_and_process(
            event_bus,
            EventType.TRIGGER_FIRED,
            {"strategy_id": 61, "strategy": strategy, "price": 100000.0},
        )
        # Частичное исполнение на 2
        await publish_and_process(
            event_bus,
            EventType.ORDER_PARTIAL_FILL,
            {
                "strategy_id": 61,
                "leg_index": 0,
                "order_id": "100",
                "filled_quantity": 2,
                "total_filled": 2,
                "remaining": 3,
            },
        )
        # После частичного исполнения ордер не перевыставляется
        # (ждём полного исполнения того же ордера)
        assert len(order_provider.placed_orders) == 1, (
            "Частичное исполнение не должно вызывать новый ордер"
        )

    @pytest.mark.asyncio
    async def test_partial_fill_then_full_fill(
        self, manager, event_bus, order_provider
    ):
        """Частичное + полное исполнение завершает ногу."""
        strategy = make_strategy(
            strategy_id=62,
            legs=[
                make_leg(leg_index=0, quantity=3),
                make_leg(leg_index=1, quantity=2),
            ],
        )
        await publish_and_process(
            event_bus,
            EventType.TRIGGER_FIRED,
            {"strategy_id": 62, "strategy": strategy, "price": 100000.0},
        )
        # Частичное исполнение на 2
        await publish_and_process(
            event_bus,
            EventType.ORDER_PARTIAL_FILL,
            {
                "strategy_id": 62,
                "leg_index": 0,
                "order_id": "100",
                "filled_quantity": 2,
                "total_filled": 2,
                "remaining": 1,
            },
        )
        assert len(order_provider.placed_orders) == 1, (
            "После частичного — без нового ордера"
        )
        # Полное исполнение ноги 0
        await publish_and_process(
            event_bus,
            EventType.ORDER_FILLED,
            {
                "strategy_id": 62,
                "leg_index": 0,
                "order_id": "100",
                "filled_quantity": 1,
                "total_filled": 3,
            },
        )
        # Должна начаться нога 1
        assert len(order_provider.placed_orders) == 2, (
            "После полного исполнения ноги 0 должен начаться нога 1"
        )


class TestMultipleStrategies:
    """Тесты параллельной работы нескольких стратегий."""

    @pytest.mark.asyncio
    async def test_two_strategies_independent(
        self, manager, event_bus, order_provider
    ):
        """Две стратегии работают независимо."""
        s1 = make_strategy(strategy_id=70, legs=[make_leg(leg_index=0, quantity=2)])
        s2 = make_strategy(strategy_id=71, legs=[make_leg(leg_index=0, quantity=3)])

        await publish_and_process(
            event_bus,
            EventType.TRIGGER_FIRED,
            {"strategy_id": 70, "strategy": s1, "price": 100000.0},
        )
        await publish_and_process(
            event_bus,
            EventType.TRIGGER_FIRED,
            {"strategy_id": 71, "strategy": s2, "price": 100000.0},
        )
        assert len(order_provider.placed_orders) == 2, (
            "Должно быть 2 ордера для 2 стратегий"
        )
        # Отменяем только стратегию 70
        await publish_and_process(
            event_bus,
            EventType.TRIGGER_DEACTIVATED,
            {"strategy_id": 70},
        )
        assert len(order_provider.cancelled_orders) == 1, (
            "Должен быть отменён только 1 ордер"
        )


class TestErrorHandling:
    """Тесты обработки ошибок."""

    @pytest.mark.asyncio
    async def test_trigger_fired_without_strategy(
        self, manager, event_bus, order_provider
    ):
        """TRIGGER_FIRED без strategy в данных — без ошибок."""
        await publish_and_process(
            event_bus,
            EventType.TRIGGER_FIRED,
            {"strategy_id": 999},
        )
        assert len(order_provider.placed_orders) == 0, (
            "Ордер не должен быть выставлен без strategy"
        )

    @pytest.mark.asyncio
    async def test_trigger_fired_for_unknown_strategy(
        self, manager, event_bus, order_provider
    ):
        """TRIGGER_FIRED для неизвестной стратегии — без ошибок."""
        strategy = make_strategy(strategy_id=100)
        await publish_and_process(
            event_bus,
            EventType.TRIGGER_FIRED,
            {"strategy_id": 100, "strategy": strategy, "price": 100000.0},
        )
        assert len(order_provider.placed_orders) == 1
        # Повторный TRIGGER_FIRED для той же стратегии — не должен
        # вызывать ошибок
        await publish_and_process(
            event_bus,
            EventType.TRIGGER_FIRED,
            {"strategy_id": 100, "strategy": strategy, "price": 100000.0},
        )
        # Должен быть только один ордер (повторная активация игнорируется)
        assert len(order_provider.placed_orders) == 1, (
            "Повторный TRIGGER_FIRED не должен выставлять новый ордер"
        )

    @pytest.mark.asyncio
    async def test_cancelled_order_without_strategy_id(
        self, manager, event_bus, order_provider
    ):
        """ORDER_CANCELLED без strategy_id — без ошибок."""
        await publish_and_process(
            event_bus,
            EventType.ORDER_CANCELLED,
            {"order_id": "999", "filled_quantity": 0},
        )
        # Не должно быть исключений


class TestConfig:
    """Тесты конфигурации."""

    @pytest.mark.asyncio
    async def test_custom_iv_threshold(
        self, event_bus, order_provider, data_provider, greeks_engine
    ):
        """Пользовательский порог IV из конфига."""
        custom_config = {
            "order_manager": {
                "iv_change_threshold": 0.05,  # 5%
            }
        }
        mgr = OrderManager(
            event_bus=event_bus,
            order_provider=order_provider,
            data_provider=data_provider,
            greeks_engine=greeks_engine,
            config=custom_config,
        )
        strategy = make_strategy(
            strategy_id=80,
            legs=[make_leg(manual_iv=0.30)],
        )
        await publish_and_process(
            event_bus,
            EventType.TRIGGER_FIRED,
            {"strategy_id": 80, "strategy": strategy, "price": 100000.0},
        )
        order = order_provider.placed_orders[0]

        # Изменение IV на 0.03 (0.30→0.33) — не превышает порог 0.05
        quote = Quote(
            instrument=order.instrument,
            bid=3300.0,
            ask=3400.0,
            implied_volatility=0.33,
        )
        await publish_and_process(
            event_bus,
            EventType.QUOTE_UPDATED,
            {"instrument": order.instrument, "quote": quote},
        )
        assert len(order_provider.modified_orders) == 0, (
            "Изменение IV 3% < порог 5% — без модификации"
        )

        # Изменение IV на 0.06 (0.30→0.36) — превышает порог 0.05
        quote2 = Quote(
            instrument=order.instrument,
            bid=3600.0,
            ask=3700.0,
            implied_volatility=0.36,
        )
        await publish_and_process(
            event_bus,
            EventType.QUOTE_UPDATED,
            {"instrument": order.instrument, "quote": quote2},
        )
        assert len(order_provider.modified_orders) == 1, (
            "Изменение IV 6% > порог 5% — должна быть модификация"
        )
