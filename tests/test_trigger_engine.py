"""Тесты для TriggerEngine (мониторинг цены БА, активация/деактивация триггера)."""

import asyncio
from typing import Any, Dict, List, Optional

import pytest

from core.event_bus import EventBus, Event, EventType
from core.providers.market_data import OptionType
from core.strategy_manager import Leg, StrategyDefinition, StrategyStatus
from core.trigger_engine import TriggerEngine


# ──────────────────────────────────────────────
# Вспомогательные классы
# ──────────────────────────────────────────────


class MockDataProvider:
    """Мок MarketDataProvider для тестов."""

    def __init__(self):
        self.prices: Dict[str, float] = {}
        self.call_count: int = 0

    async def get_futures_price(self, base_asset: str) -> Optional[float]:
        self.call_count += 1
        return self.prices.get(base_asset)

    def set_price(self, base_asset: str, price: float):
        self.prices[base_asset] = price


# ──────────────────────────────────────────────
# Фикстуры
# ──────────────────────────────────────────────


@pytest.fixture
def event_bus():
    """Создать реальный EventBus."""
    return EventBus()


@pytest.fixture
def data_provider():
    """Создать мок провайдера данных."""
    return MockDataProvider()


@pytest.fixture
def config():
    """Конфигурация для тестов (маленький polling_interval)."""
    return {
        "strategies": {
            "polling_interval_sec": 0.05,
            "trigger_deactivation_default": 100,
        }
    }


@pytest.fixture
async def engine(event_bus, data_provider, config):
    """Создать TriggerEngine."""
    eng = TriggerEngine(event_bus, data_provider, config)
    yield eng
    await eng.stop()


@pytest.fixture
def strategy():
    """Создать тестовую стратегию."""
    return StrategyDefinition(
        strategy_id=1,
        name="Тестовая стратегия",
        base_asset="Si",
        status=StrategyStatus.ACTIVE,
        legs=[
            Leg(leg_index=0, option_type=OptionType.CALL, strike=100000, sign=1, quantity=1)
        ],
        trigger_level=75000.0,
        trigger_deactivation_threshold=100.0,
    )


@pytest.fixture
def strategy2():
    """Вторая тестовая стратегия (для тестов с несколькими стратегиями)."""
    return StrategyDefinition(
        strategy_id=2,
        name="Тестовая стратегия 2",
        base_asset="Si",
        status=StrategyStatus.ACTIVE,
        legs=[
            Leg(leg_index=0, option_type=OptionType.PUT, strike=90000, sign=-1, quantity=2)
        ],
        trigger_level=74000.0,
        trigger_deactivation_threshold=100.0,
    )


# ──────────────────────────────────────────────
# Тесты: start_monitoring
# ──────────────────────────────────────────────


class TestStartMonitoring:
    """Тесты метода start_monitoring."""

    async def test_start_monitoring_adds_strategy(self, engine, strategy):
        """Добавление стратегии в мониторинг."""
        assert engine.active_count() == 0
        assert not engine.is_monitoring(strategy.strategy_id)

        await engine.start_monitoring(strategy)

        assert engine.active_count() == 1
        assert engine.is_monitoring(strategy.strategy_id)

    async def test_start_monitoring_starts_polling(self, engine, strategy):
        """Запуск цикла опроса при добавлении первой стратегии."""
        assert engine._polling_task is None

        await engine.start_monitoring(strategy)

        assert engine._polling_task is not None
        assert not engine._polling_task.done()

    async def test_start_monitoring_does_not_duplicate_task(self, engine, strategy, strategy2):
        """Повторный вызов не создаёт второй polling_task."""
        await engine.start_monitoring(strategy)
        task1 = engine._polling_task

        await engine.start_monitoring(strategy2)
        task2 = engine._polling_task

        assert task1 is task2, "Должен использоваться тот же polling_task"

    async def test_start_monitoring_twice_same_strategy(self, engine, strategy):
        """Добавление одной стратегии дважды не увеличивает счётчик."""
        await engine.start_monitoring(strategy)
        assert engine.active_count() == 1

        await engine.start_monitoring(strategy)
        assert engine.active_count() == 1


# ──────────────────────────────────────────────
# Тесты: stop_monitoring
# ──────────────────────────────────────────────


class TestStopMonitoring:
    """Тесты метода stop_monitoring."""

    async def test_stop_monitoring_removes_strategy(self, engine, strategy, strategy2):
        """Удаление стратегии из мониторинга."""
        await engine.start_monitoring(strategy)
        await engine.start_monitoring(strategy2)
        assert engine.active_count() == 2

        await engine.stop_monitoring(strategy.strategy_id)

        assert engine.active_count() == 1
        assert not engine.is_monitoring(strategy.strategy_id)
        assert engine.is_monitoring(strategy2.strategy_id)

    async def test_stop_monitoring_stops_polling_when_empty(self, engine, strategy):
        """При удалении последней стратегии polling_task останавливается."""
        await engine.start_monitoring(strategy)
        assert engine._polling_task is not None

        await engine.stop_monitoring(strategy.strategy_id)

        assert engine.active_count() == 0
        assert engine._polling_task is None

    async def test_stop_monitoring_unknown_strategy(self, engine):
        """Удаление неизвестной стратегии не вызывает ошибку."""
        await engine.stop_monitoring(999)  # не должно упасть


# ──────────────────────────────────────────────
# Тесты: polling loop
# ──────────────────────────────────────────────


class TestPollingLoop:
    """Тесты цикла опроса цен."""

    async def test_polling_loop_fetches_prices(self, event_bus, data_provider, config, strategy):
        """Цикл опроса запрашивает цены у data_provider."""
        engine = TriggerEngine(event_bus, data_provider, config)
        data_provider.set_price("Si", 74000.0)

        await engine.start_monitoring(strategy)
        await asyncio.sleep(0.15)  # даём циклу пару итераций
        await engine.stop()

        assert data_provider.call_count >= 1, "Должен быть хотя бы один запрос цены"

    async def test_no_trigger_when_price_none(self, event_bus, data_provider, config, strategy):
        """При отсутствии данных (None) триггер не срабатывает."""
        collected_events = []

        async def collector(event: Event):
            collected_events.append(event)

        event_bus.subscribe(EventType.TRIGGER_FIRED, collector)
        event_bus.subscribe(EventType.TRIGGER_DEACTIVATED, collector)

        engine = TriggerEngine(event_bus, data_provider, config)
        # Не устанавливаем цену — провайдер вернёт None

        await engine.start_monitoring(strategy)
        await asyncio.sleep(0.15)
        await engine.stop()

        assert len(collected_events) == 0


# ──────────────────────────────────────────────
# Тесты: активация триггера (TRIGGER_FIRED)
# ──────────────────────────────────────────────


class TestTriggerFired:
    """Тесты активации триггера при касании уровня."""

    async def test_trigger_fired_when_price_reaches_level(self, event_bus, data_provider, config, strategy):
        """При достижении цены БА уровня триггера публикуется TRIGGER_FIRED."""
        triggered_events = []

        async def on_trigger(event: Event):
            triggered_events.append(event)

        event_bus.subscribe(EventType.TRIGGER_FIRED, on_trigger)

        engine = TriggerEngine(event_bus, data_provider, config)
        data_provider.set_price("Si", 75000.0)  # цена == trigger_level

        await engine.start_monitoring(strategy)
        await asyncio.sleep(0.15)
        await engine.stop()

        assert len(triggered_events) == 1
        assert triggered_events[0].data["strategy_id"] == strategy.strategy_id
        assert triggered_events[0].data["price"] == 75000.0

    async def test_trigger_fired_when_price_above_level(self, event_bus, data_provider, config, strategy):
        """При цене выше уровня (но в пределах порога) триггер активируется."""
        triggered_events = []

        async def on_trigger(event: Event):
            triggered_events.append(event)

        event_bus.subscribe(EventType.TRIGGER_FIRED, on_trigger)

        engine = TriggerEngine(event_bus, data_provider, config)

        # Цена чуть выше trigger_level (75000), но в пределах порога (100)
        # |75010 - 75000| = 10 <= 100 → не будет деактивации
        data_provider.set_price("Si", 75010.0)

        await engine.start_monitoring(strategy)
        await asyncio.sleep(0.15)
        await engine.stop()

        assert len(triggered_events) == 1

    async def test_trigger_not_fired_below_level(self, event_bus, data_provider, config, strategy):
        """При цене ниже уровня триггер не срабатывает."""
        triggered_events = []

        async def on_trigger(event: Event):
            triggered_events.append(event)

        event_bus.subscribe(EventType.TRIGGER_FIRED, on_trigger)

        engine = TriggerEngine(event_bus, data_provider, config)
        data_provider.set_price("Si", 74000.0)  # цена < trigger_level

        await engine.start_monitoring(strategy)
        await asyncio.sleep(0.15)
        await engine.stop()

        assert len(triggered_events) == 0

    async def test_trigger_fired_only_once(self, event_bus, data_provider, config, strategy):
        """Триггер срабатывает только один раз (пока не деактивируется)."""
        triggered_events = []

        async def on_trigger(event: Event):
            triggered_events.append(event)

        event_bus.subscribe(EventType.TRIGGER_FIRED, on_trigger)

        engine = TriggerEngine(event_bus, data_provider, config)
        # Цена в пределах порога от trigger_level — сработает,
        # но не будет деактивироваться
        data_provider.set_price("Si", 75010.0)

        await engine.start_monitoring(strategy)
        await asyncio.sleep(0.2)  # несколько итераций цикла
        await engine.stop()

        assert len(triggered_events) == 1, "Триггер должен сработать только один раз"


# ──────────────────────────────────────────────
# Тесты: деактивация триггера (TRIGGER_DEACTIVATED)
# ──────────────────────────────────────────────


class TestTriggerDeactivated:
    """Тесты деактивации триггера при уходе цены за порог."""

    async def test_trigger_deactivated_when_price_diverges(
        self, event_bus, data_provider, config, strategy
    ):
        """При уходе цены за порог деактивации публикуется TRIGGER_DEACTIVATED."""
        fired_events = []
        deactivated_events = []

        async def on_fired(event: Event):
            fired_events.append(event)

        async def on_deactivated(event: Event):
            deactivated_events.append(event)

        event_bus.subscribe(EventType.TRIGGER_FIRED, on_fired)
        event_bus.subscribe(EventType.TRIGGER_DEACTIVATED, on_deactivated)

        engine = TriggerEngine(event_bus, data_provider, config)

        # Шаг 1: цена в пределах порога — триггер сработает, но не деактивируется
        # trigger_level=75000, threshold=100, цена 75050 → |75050-75000|=50 <= 100
        data_provider.set_price("Si", 75050.0)

        await engine.start_monitoring(strategy)
        await asyncio.sleep(0.1)

        # Проверяем что триггер сработал и нет деактивации
        assert len(fired_events) == 1
        assert len(deactivated_events) == 0

        # Шаг 2: цена уходит за порог
        # |74000 - 75000| = 1000 > 100 → деактивация
        data_provider.set_price("Si", 74000.0)

        await asyncio.sleep(0.15)
        await engine.stop()

        assert len(deactivated_events) == 1, "Должна произойти деактивация"
        assert deactivated_events[0].data["strategy_id"] == strategy.strategy_id
        assert deactivated_events[0].data["price"] == 74000.0

    async def test_trigger_not_deactivated_within_threshold(
        self, event_bus, data_provider, config, strategy
    ):
        """При цене в пределах порога деактивация не происходит."""
        deactivated_events = []

        async def on_deactivated(event: Event):
            deactivated_events.append(event)

        event_bus.subscribe(EventType.TRIGGER_DEACTIVATED, on_deactivated)

        engine = TriggerEngine(event_bus, data_provider, config)
        # Сначала срабатываем триггер
        data_provider.set_price("Si", 75100.0)  # цена = trigger_level (75000) + часть threshold (100)

        await engine.start_monitoring(strategy)
        await asyncio.sleep(0.1)

        # Меняем цену в пределах порога
        # |74950 - 75000| = 50 <= 100 → внутри порога
        data_provider.set_price("Si", 74950.0)

        await asyncio.sleep(0.15)
        await engine.stop()

        assert len(deactivated_events) == 0, "Не должно быть деактивации в пределах порога"

    async def test_trigger_deactivated_only_once_within_threshold_cycle(
        self, event_bus, data_provider, config, strategy
    ):
        """Проверка цикла: сработал → ушёл → деактивировался → снова может сработать."""
        events = []

        async def collector(event: Event):
            events.append((event.type, event.data))

        event_bus.subscribe(EventType.TRIGGER_FIRED, collector)
        event_bus.subscribe(EventType.TRIGGER_DEACTIVATED, collector)

        engine = TriggerEngine(event_bus, data_provider, config)

        # Шаг 1: цена достигает уровня → TRIGGER_FIRED
        data_provider.set_price("Si", 75000.0)
        await engine.start_monitoring(strategy)
        await asyncio.sleep(0.1)

        assert len(events) == 1
        assert events[0][0] == EventType.TRIGGER_FIRED

        # Шаг 2: цена уходит за порог → TRIGGER_DEACTIVATED
        data_provider.set_price("Si", 74000.0)
        await asyncio.sleep(0.15)

        assert len(events) == 2
        assert events[1][0] == EventType.TRIGGER_DEACTIVATED

        # Шаг 3: цена снова достигает уровня → ещё один TRIGGER_FIRED
        data_provider.set_price("Si", 75000.0)
        await asyncio.sleep(0.15)

        assert len(events) == 3
        assert events[2][0] == EventType.TRIGGER_FIRED

        await engine.stop()


# ──────────────────────────────────────────────
# Тесты: несколько стратегий
# ──────────────────────────────────────────────


class TestMultipleStrategies:
    """Тесты параллельного мониторинга нескольких стратегий."""

    async def test_multiple_strategies_monitoring(
        self, event_bus, data_provider, config
    ):
        """Параллельный мониторинг нескольких стратегий с разными уровнями."""
        # Создаём две стратегии с одинаковым БА, но разными уровнями триггера
        # и достаточно большими порогами, чтобы не было ложной деактивации
        s1 = StrategyDefinition(
            strategy_id=10,
            name="Стратегия A",
            base_asset="Si",
            status=StrategyStatus.ACTIVE,
            legs=[Leg(leg_index=0, option_type=OptionType.CALL, strike=100000, sign=1, quantity=1)],
            trigger_level=75000.0,
            trigger_deactivation_threshold=500.0,  # большой порог
        )
        s2 = StrategyDefinition(
            strategy_id=11,
            name="Стратегия B",
            base_asset="Si",
            status=StrategyStatus.ACTIVE,
            legs=[Leg(leg_index=0, option_type=OptionType.PUT, strike=90000, sign=-1, quantity=2)],
            trigger_level=74000.0,
            trigger_deactivation_threshold=2000.0,  # большой порог для стабильности
        )

        fired_events = []

        async def on_fired(event: Event):
            fired_events.append(event)

        event_bus.subscribe(EventType.TRIGGER_FIRED, on_fired)

        engine = TriggerEngine(event_bus, data_provider, config)

        # Цена выше обоих уровней, но в пределах порога деактивации
        data_provider.set_price("Si", 75100.0)

        await engine.start_monitoring(s1)
        await engine.start_monitoring(s2)
        await asyncio.sleep(0.15)
        await engine.stop()

        # Обе стратегии должны сработать ровно по одному разу
        fired_ids = [e.data["strategy_id"] for e in fired_events]
        assert 10 in fired_ids
        assert 11 in fired_ids
        assert len(fired_events) == 2


# ──────────────────────────────────────────────
# Тесты: stop()
# ──────────────────────────────────────────────


class TestStop:
    """Тесты метода stop()."""

    async def test_stop_cleans_up(self, engine, strategy):
        """stop() очищает все активные стратегии и останавливает поллинг."""
        await engine.start_monitoring(strategy)
        assert engine.active_count() == 1
        assert engine._polling_task is not None

        await engine.stop()

        assert engine.active_count() == 0
        assert engine._polling_task is None
        assert not engine._running

    async def test_stop_idempotent(self, engine):
        """Повторный вызов stop() безопасен."""
        await engine.stop()
        await engine.stop()  # не должно упасть


# ──────────────────────────────────────────────
# Тесты: вспомогательные методы
# ──────────────────────────────────────────────


class TestHelpers:
    """Тесты вспомогательных методов."""

    async def test_is_monitoring(self, engine, strategy):
        """Проверка статуса мониторинга."""
        assert not engine.is_monitoring(strategy.strategy_id)

        await engine.start_monitoring(strategy)
        assert engine.is_monitoring(strategy.strategy_id)

        await engine.stop_monitoring(strategy.strategy_id)
        assert not engine.is_monitoring(strategy.strategy_id)

    async def test_active_count(self, engine, strategy, strategy2):
        """Количество мониторимых стратегий."""
        assert engine.active_count() == 0

        await engine.start_monitoring(strategy)
        assert engine.active_count() == 1

        await engine.start_monitoring(strategy2)
        assert engine.active_count() == 2

        await engine.stop_monitoring(strategy.strategy_id)
        assert engine.active_count() == 1


# ──────────────────────────────────────────────
# Тесты: данные событий
# ──────────────────────────────────────────────


class TestEventData:
    """Тесты корректности данных в событиях."""

    async def test_event_data_contains_correct_fields(self, event_bus, data_provider, config, strategy):
        """Данные события TRIGGER_FIRED содержат корректные поля."""
        triggered_events = []

        async def on_trigger(event: Event):
            triggered_events.append(event)

        event_bus.subscribe(EventType.TRIGGER_FIRED, on_trigger)

        engine = TriggerEngine(event_bus, data_provider, config)
        data_provider.set_price("Si", 75000.0)

        await engine.start_monitoring(strategy)
        await asyncio.sleep(0.15)
        await engine.stop()

        assert len(triggered_events) == 1
        event = triggered_events[0]
        assert "strategy_id" in event.data
        assert "price" in event.data
        assert event.data["strategy_id"] == 1
        assert event.data["price"] == 75000.0

    async def test_deactivation_event_data_contains_correct_fields(
        self, event_bus, data_provider, config, strategy
    ):
        """Данные события TRIGGER_DEACTIVATED содержат корректные поля."""
        deactivated_events = []

        async def on_deactivated(event: Event):
            deactivated_events.append(event)

        event_bus.subscribe(EventType.TRIGGER_DEACTIVATED, on_deactivated)

        engine = TriggerEngine(event_bus, data_provider, config)

        # Шаг 1: цена в пределах порога — триггер сработает
        data_provider.set_price("Si", 75050.0)
        await engine.start_monitoring(strategy)
        await asyncio.sleep(0.1)

        # Шаг 2: уводим цену за порог деактивации
        data_provider.set_price("Si", 74000.0)
        await asyncio.sleep(0.15)
        await engine.stop()

        assert len(deactivated_events) == 1
        event = deactivated_events[0]
        assert "strategy_id" in event.data
        assert "price" in event.data
        assert event.data["strategy_id"] == 1
        assert event.data["price"] == 74000.0
