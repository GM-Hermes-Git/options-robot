"""Тесты для StrategyManager (управление жизненным циклом стратегий)."""

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List

import pytest

from core.event_bus import EventBus, Event, EventType
from core.providers.market_data import OptionType
from core.strategy_manager import (
    Leg,
    StrategyDefinition,
    StrategyManager,
    StrategyStatus,
)
from database.db_manager import DatabaseManager


@pytest.fixture
async def event_bus():
    """Создать реальный EventBus."""
    bus = EventBus()
    yield bus
    # Проверяем, что после теста не осталось «висящих» задач
    # (не обязательно, но на всякий случай)


@pytest.fixture
async def db_manager():
    """Создать DatabaseManager с :memory: БД."""
    manager = DatabaseManager(":memory:")
    await manager.initialize()
    yield manager
    await manager.close()


@pytest.fixture
async def strategy_manager(event_bus, db_manager):
    """Создать StrategyManager без вызова initialize()."""
    sm = StrategyManager(event_bus=event_bus, db_manager=db_manager, config={})
    yield sm
    # Отменяем фоновую задачу, если она запущена
    if sm._check_task is not None and not sm._check_task.done():
        sm._check_task.cancel()


@pytest.fixture
def sample_strategy():
    """Создать валидную стратегию для тестов."""
    legs = [
        Leg(leg_index=0, option_type=OptionType.CALL, strike=85000.0, sign=1, quantity=1),
        Leg(leg_index=1, option_type=OptionType.PUT, strike=82000.0, sign=1, quantity=1),
    ]
    return StrategyDefinition(
        name="Si straddle",
        base_asset="Si",
        legs=legs,
        trigger_level=84000.0,
    )


@pytest.fixture
def sample_strategy_single_leg():
    """Создать стратегию с одной ногой для проверки BUILDING → POSITION_OPEN."""
    legs = [
        Leg(leg_index=0, option_type=OptionType.CALL, strike=85000.0, sign=1, quantity=1),
    ]
    return StrategyDefinition(
        name="Si single leg",
        base_asset="Si",
        legs=legs,
        trigger_level=84000.0,
    )


class TestStrategyManager:
    """Тесты для StrategyManager."""

    # ──────────────────────────────────────────────
    # create_strategy
    # ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_create_strategy(self, strategy_manager, sample_strategy):
        """Создание стратегии возвращает ID > 0."""
        strategy_id = await strategy_manager.create_strategy(sample_strategy)
        assert strategy_id > 0, "ID новой стратегии должен быть > 0"

        # Стратегия должна быть доступна в локальном словаре
        loaded = strategy_manager.get_strategy(strategy_id)
        assert loaded is not None
        assert loaded.name == sample_strategy.name
        assert loaded.strategy_id == strategy_id

    @pytest.mark.asyncio
    async def test_create_strategy_invalid_definition(self, strategy_manager):
        """Создание с невалидным определением вызывает ValueError."""
        with pytest.raises(ValueError, match="name must not be empty"):
            invalid = StrategyDefinition(
                name="",
                base_asset="Si",
                legs=[Leg(leg_index=0, option_type=OptionType.CALL, strike=85000.0, sign=1, quantity=1)],
                trigger_level=84000.0,
            )
            await strategy_manager.create_strategy(invalid)

    # ──────────────────────────────────────────────
    # start_strategy
    # ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_start_strategy_configured_immediate(self, strategy_manager, sample_strategy):
        """CONFIGURED → ACTIVE (без start_time)."""
        strategy_id = await strategy_manager.create_strategy(sample_strategy)
        result = await strategy_manager.start_strategy(strategy_id)
        assert result is True

        strategy = strategy_manager.get_strategy(strategy_id)
        assert strategy is not None
        assert strategy.status == StrategyStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_start_strategy_configured_with_future_start_time(self, strategy_manager, sample_strategy):
        """CONFIGURED → WAITING если start_time в будущем."""
        # Создаём стратегию с start_time = +1 час
        sample_strategy.start_time = datetime.now() + timedelta(hours=1)
        strategy_id = await strategy_manager.create_strategy(sample_strategy)

        result = await strategy_manager.start_strategy(strategy_id)
        assert result is True

        strategy = strategy_manager.get_strategy(strategy_id)
        assert strategy is not None
        assert strategy.status == StrategyStatus.WAITING, (
            f"Ожидался WAITING, получен {strategy.status}"
        )

    @pytest.mark.asyncio
    async def test_start_strategy_not_configured(self, strategy_manager, sample_strategy):
        """Если статус не CONFIGURED, start_strategy возвращает False."""
        strategy_id = await strategy_manager.create_strategy(sample_strategy)

        # Сначала запускаем → становится ACTIVE
        await strategy_manager.start_strategy(strategy_id)

        # Повторный запуск должен вернуть False (уже не CONFIGURED)
        result = await strategy_manager.start_strategy(strategy_id)
        assert result is False

    @pytest.mark.asyncio
    async def test_start_strategy_with_past_start_time(self, strategy_manager, sample_strategy):
        """Если start_time в прошлом → сразу ACTIVE."""
        sample_strategy.start_time = datetime.now() - timedelta(hours=1)
        strategy_id = await strategy_manager.create_strategy(sample_strategy)

        result = await strategy_manager.start_strategy(strategy_id)
        assert result is True

        strategy = strategy_manager.get_strategy(strategy_id)
        assert strategy is not None
        assert strategy.status == StrategyStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_start_nonexistent_strategy(self, strategy_manager):
        """Запуск несуществующей стратегии возвращает False."""
        result = await strategy_manager.start_strategy(999)
        assert result is False

    # ──────────────────────────────────────────────
    # stop_strategy
    # ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_stop_strategy(self, strategy_manager, sample_strategy):
        """Любой статус → STOPPED."""
        strategy_id = await strategy_manager.create_strategy(sample_strategy)
        await strategy_manager.start_strategy(strategy_id)  # теперь ACTIVE

        result = await strategy_manager.stop_strategy(strategy_id)
        assert result is True

        strategy = strategy_manager.get_strategy(strategy_id)
        assert strategy is not None
        assert strategy.status == StrategyStatus.STOPPED

    @pytest.mark.asyncio
    async def test_stop_already_stopped(self, strategy_manager, sample_strategy):
        """Остановка уже остановленной стратегии возвращает False."""
        strategy_id = await strategy_manager.create_strategy(sample_strategy)
        await strategy_manager.start_strategy(strategy_id)
        await strategy_manager.stop_strategy(strategy_id)  # первая остановка

        result = await strategy_manager.stop_strategy(strategy_id)
        assert result is False

    @pytest.mark.asyncio
    async def test_stop_nonexistent_strategy(self, strategy_manager):
        """Остановка несуществующей стратегии возвращает False."""
        result = await strategy_manager.stop_strategy(999)
        assert result is False

    @pytest.mark.asyncio
    async def test_stop_strategy_from_triggered(self, strategy_manager, sample_strategy):
        """Остановка из статуса TRIGGERED → STOPPED."""
        strategy_id = await strategy_manager.create_strategy(sample_strategy)
        await strategy_manager.start_strategy(strategy_id)  # ACTIVE
        # Переводим в TRIGGERED через событие
        await strategy_manager._event_bus.publish(
            EventType.TRIGGER_FIRED, {"strategy_id": strategy_id}
        )
        await asyncio.sleep(0.01)  # даём время обработчику сработать

        strategy = strategy_manager.get_strategy(strategy_id)
        assert strategy.status == StrategyStatus.TRIGGERED

        result = await strategy_manager.stop_strategy(strategy_id)
        assert result is True
        assert strategy_manager.get_strategy(strategy_id).status == StrategyStatus.STOPPED

    # ──────────────────────────────────────────────
    # get_strategy / get_all_strategies / get_strategies_by_status
    # ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_strategy(self, strategy_manager, sample_strategy):
        """Получение стратегии по ID."""
        strategy_id = await strategy_manager.create_strategy(sample_strategy)
        strategy = strategy_manager.get_strategy(strategy_id)
        assert strategy is not None
        assert strategy.strategy_id == strategy_id
        assert strategy.name == sample_strategy.name

    @pytest.mark.asyncio
    async def test_get_strategy_nonexistent(self, strategy_manager):
        """Получение несуществующей стратегии возвращает None."""
        strategy = strategy_manager.get_strategy(999)
        assert strategy is None

    @pytest.mark.asyncio
    async def test_get_all_strategies(self, strategy_manager, sample_strategy):
        """Получение всех стратегий."""
        id1 = await strategy_manager.create_strategy(sample_strategy)
        id2 = await strategy_manager.create_strategy(sample_strategy)

        all_strategies = strategy_manager.get_all_strategies()
        assert len(all_strategies) == 2
        ids = [s.strategy_id for s in all_strategies]
        assert id1 in ids
        assert id2 in ids

    @pytest.mark.asyncio
    async def test_get_strategies_by_status(self, strategy_manager, sample_strategy):
        """Фильтрация стратегий по статусу."""
        id1 = await strategy_manager.create_strategy(sample_strategy)
        await strategy_manager.start_strategy(id1)  # ACTIVE

        id2 = await strategy_manager.create_strategy(sample_strategy)
        # id2 остаётся CONFIGURED

        active_strategies = strategy_manager.get_strategies_by_status(StrategyStatus.ACTIVE)
        assert len(active_strategies) == 1
        assert active_strategies[0].strategy_id == id1

        configured_strategies = strategy_manager.get_strategies_by_status(StrategyStatus.CONFIGURED)
        assert len(configured_strategies) == 1
        assert configured_strategies[0].strategy_id == id2

    @pytest.mark.asyncio
    async def test_get_strategies_by_status_empty(self, strategy_manager):
        """Поиск по статусу при отсутствии стратегий."""
        result = strategy_manager.get_strategies_by_status(StrategyStatus.ACTIVE)
        assert result == []

    # ──────────────────────────────────────────────
    # Проверка публикации событий через EventBus
    # ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_stop_strategy_publishes_event(self, strategy_manager, sample_strategy):
        """stop_strategy публикует STRATEGY_STOPPED."""
        # Подписываемся на STRATEGY_STOPPED для проверки
        received_events: List[Event] = []

        async def on_stopped(event: Event):
            received_events.append(event)

        strategy_manager._event_bus.subscribe(EventType.STRATEGY_STOPPED, on_stopped)

        strategy_id = await strategy_manager.create_strategy(sample_strategy)
        await strategy_manager.start_strategy(strategy_id)
        await strategy_manager.stop_strategy(strategy_id)

        # Даём время асинхронному обработчику
        await asyncio.sleep(0.01)

        assert len(received_events) == 1
        assert received_events[0].type == EventType.STRATEGY_STOPPED
        assert received_events[0].data.get("strategy_id") == strategy_id

    # ──────────────────────────────────────────────
    # Обработчики событий
    # ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_trigger_fired_changes_status_to_triggered(self, strategy_manager, sample_strategy):
        """TRIGGER_FIRED: ACTIVE → TRIGGERED."""
        strategy_id = await strategy_manager.create_strategy(sample_strategy)
        await strategy_manager.start_strategy(strategy_id)  # ACTIVE

        # Публикуем TRIGGER_FIRED
        await strategy_manager._event_bus.publish(
            EventType.TRIGGER_FIRED,
            {"strategy_id": strategy_id},
        )
        await asyncio.sleep(0.01)

        strategy = strategy_manager.get_strategy(strategy_id)
        assert strategy is not None
        assert strategy.status == StrategyStatus.TRIGGERED

    @pytest.mark.asyncio
    async def test_trigger_fired_ignored_if_not_active(self, strategy_manager, sample_strategy):
        """TRIGGER_FIRED игнорируется, если стратегия не в статусе ACTIVE."""
        strategy_id = await strategy_manager.create_strategy(sample_strategy)
        # Стратегия в CONFIGURED — триггер не должен сработать

        await strategy_manager._event_bus.publish(
            EventType.TRIGGER_FIRED,
            {"strategy_id": strategy_id},
        )
        await asyncio.sleep(0.01)

        strategy = strategy_manager.get_strategy(strategy_id)
        assert strategy is not None
        assert strategy.status == StrategyStatus.CONFIGURED  # статус не изменился

    @pytest.mark.asyncio
    async def test_trigger_fired_unknown_strategy(self, strategy_manager):
        """TRIGGER_FIRED для неизвестной стратегии не вызывает ошибку."""
        await strategy_manager._event_bus.publish(
            EventType.TRIGGER_FIRED,
            {"strategy_id": 999},
        )
        await asyncio.sleep(0.01)
        # Просто проверяем, что нет исключения

    @pytest.mark.asyncio
    async def test_order_filled_changes_triggered_to_building(
        self, strategy_manager, sample_strategy_single_leg
    ):
        """ORDER_FILLED: TRIGGERED → BUILDING."""
        strategy_id = await strategy_manager.create_strategy(sample_strategy_single_leg)
        await strategy_manager.start_strategy(strategy_id)  # ACTIVE
        await strategy_manager._event_bus.publish(
            EventType.TRIGGER_FIRED, {"strategy_id": strategy_id}
        )
        await asyncio.sleep(0.01)  # TRIGGERED

        # ORDER_FILLED
        await strategy_manager._event_bus.publish(
            EventType.ORDER_FILLED,
            {"strategy_id": strategy_id, "leg_index": 0},
        )
        await asyncio.sleep(0.01)

        strategy = strategy_manager.get_strategy(strategy_id)
        assert strategy is not None
        assert strategy.status == StrategyStatus.BUILDING

    @pytest.mark.asyncio
    async def test_strategy_transition_through_order_filled(
        self, strategy_manager, sample_strategy_single_leg
    ):
        """Полный цикл: TRIGGERED → BUILDING → POSITION_OPEN через ORDER_FILLED."""
        strategy_id = await strategy_manager.create_strategy(sample_strategy_single_leg)
        await strategy_manager.start_strategy(strategy_id)  # ACTIVE
        await strategy_manager._event_bus.publish(
            EventType.TRIGGER_FIRED, {"strategy_id": strategy_id}
        )
        await asyncio.sleep(0.01)  # TRIGGERED

        # Первый ORDER_FILLED: TRIGGERED → BUILDING
        await strategy_manager._event_bus.publish(
            EventType.ORDER_FILLED,
            {"strategy_id": strategy_id, "leg_index": 0},
        )
        await asyncio.sleep(0.01)
        assert strategy_manager.get_strategy(strategy_id).status == StrategyStatus.BUILDING

        # Второй ORDER_FILLED (та же нога, но для single_leg одна нога — уже заполнена):
        # BUILDING → POSITION_OPEN
        await strategy_manager._event_bus.publish(
            EventType.ORDER_FILLED,
            {"strategy_id": strategy_id, "leg_index": 0},
        )
        await asyncio.sleep(0.01)
        strategy = strategy_manager.get_strategy(strategy_id)
        assert strategy.status == StrategyStatus.POSITION_OPEN, (
            f"Ожидался POSITION_OPEN, получен {strategy.status}"
        )

    @pytest.mark.asyncio
    async def test_order_partial_fill_handled(
        self, strategy_manager, sample_strategy_single_leg
    ):
        """ORDER_PARTIAL_FILL также обрабатывается (подписка на оба события)."""
        strategy_id = await strategy_manager.create_strategy(sample_strategy_single_leg)
        await strategy_manager.start_strategy(strategy_id)
        await strategy_manager._event_bus.publish(
            EventType.TRIGGER_FIRED, {"strategy_id": strategy_id}
        )
        await asyncio.sleep(0.01)

        # ORDER_PARTIAL_FILL должен работать так же, как ORDER_FILLED
        await strategy_manager._event_bus.publish(
            EventType.ORDER_PARTIAL_FILL,
            {"strategy_id": strategy_id, "leg_index": 0},
        )
        await asyncio.sleep(0.01)

        strategy = strategy_manager.get_strategy(strategy_id)
        assert strategy.status == StrategyStatus.BUILDING, (
            f"Ожидался BUILDING после ORDER_PARTIAL_FILL, получен {strategy.status}"
        )

    @pytest.mark.asyncio
    async def test_order_filled_ignored_if_not_triggered(
        self, strategy_manager, sample_strategy
    ):
        """ORDER_FILLED игнорируется, если стратегия не TRIGGERED/BUILDING."""
        strategy_id = await strategy_manager.create_strategy(sample_strategy)
        await strategy_manager.start_strategy(strategy_id)  # ACTIVE

        await strategy_manager._event_bus.publish(
            EventType.ORDER_FILLED,
            {"strategy_id": strategy_id, "leg_index": 0},
        )
        await asyncio.sleep(0.01)

        strategy = strategy_manager.get_strategy(strategy_id)
        assert strategy is not None
        assert strategy.status == StrategyStatus.ACTIVE  # статус не изменился

    @pytest.mark.asyncio
    async def test_position_updated_closes_position(
        self, strategy_manager, sample_strategy_single_leg
    ):
        """POSITION_UPDATED с признаком закрытия → STOPPED."""
        strategy_id = await strategy_manager.create_strategy(sample_strategy_single_leg)
        await strategy_manager.start_strategy(strategy_id)
        await strategy_manager._event_bus.publish(
            EventType.TRIGGER_FIRED, {"strategy_id": strategy_id}
        )
        await asyncio.sleep(0.01)
        # Переводим в POSITION_OPEN
        await strategy_manager._event_bus.publish(
            EventType.ORDER_FILLED,
            {"strategy_id": strategy_id, "leg_index": 0},
        )
        await asyncio.sleep(0.01)

        # Ещё один fill для single_leg
        await strategy_manager._event_bus.publish(
            EventType.ORDER_FILLED,
            {"strategy_id": strategy_id, "leg_index": 0},
        )
        await asyncio.sleep(0.01)
        assert strategy_manager.get_strategy(strategy_id).status == StrategyStatus.POSITION_OPEN

        # POSITION_UPDATED с closed=True
        await strategy_manager._event_bus.publish(
            EventType.POSITION_UPDATED,
            {"strategy_id": strategy_id, "closed": True},
        )
        await asyncio.sleep(0.01)

        strategy = strategy_manager.get_strategy(strategy_id)
        assert strategy is not None
        assert strategy.status == StrategyStatus.STOPPED, (
            f"Ожидался STOPPED после закрытия позиции, получен {strategy.status}"
        )

    @pytest.mark.asyncio
    async def test_position_updated_ignored_if_not_open(
        self, strategy_manager, sample_strategy
    ):
        """POSITION_UPDATED игнорируется, если стратегия не POSITION_OPEN."""
        strategy_id = await strategy_manager.create_strategy(sample_strategy)
        # Стратегия CONFIGURED

        await strategy_manager._event_bus.publish(
            EventType.POSITION_UPDATED,
            {"strategy_id": strategy_id, "closed": True},
        )
        await asyncio.sleep(0.01)

        strategy = strategy_manager.get_strategy(strategy_id)
        assert strategy is not None
        assert strategy.status == StrategyStatus.CONFIGURED  # не изменился

    # ──────────────────────────────────────────────
    # initialize
    # ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_initialize_loads_from_db(self, event_bus, db_manager, sample_strategy):
        """При инициализации загружаются стратегии из БД."""
        # Сохраняем стратегию напрямую в БД
        strategy_id = await db_manager.save_strategy(sample_strategy)

        # Создаём StrategyManager с уже заполненной БД
        sm = StrategyManager(event_bus=event_bus, db_manager=db_manager, config={})
        await sm.initialize()

        try:
            # Проверяем, что стратегия загружена
            loaded = sm.get_strategy(strategy_id)
            assert loaded is not None
            assert loaded.name == sample_strategy.name
            assert loaded.strategy_id == strategy_id
        finally:
            if sm._check_task is not None and not sm._check_task.done():
                sm._check_task.cancel()

    @pytest.mark.asyncio
    async def test_initialize_empty_db(self, event_bus, db_manager):
        """Инициализация с пустой БД не вызывает ошибок."""
        sm = StrategyManager(event_bus=event_bus, db_manager=db_manager, config={})
        await sm.initialize()

        try:
            all_strategies = sm.get_all_strategies()
            assert all_strategies == []
        finally:
            if sm._check_task is not None and not sm._check_task.done():
                sm._check_task.cancel()

    @pytest.mark.asyncio
    async def test_initialize_loads_multiple_strategies(
        self, event_bus, db_manager, sample_strategy
    ):
        """Загрузка нескольких стратегий при инициализации."""
        id1 = await db_manager.save_strategy(sample_strategy)
        id2 = await db_manager.save_strategy(sample_strategy)

        sm = StrategyManager(event_bus=event_bus, db_manager=db_manager, config={})
        await sm.initialize()

        try:
            all_strategies = sm.get_all_strategies()
            assert len(all_strategies) == 2
            ids = [s.strategy_id for s in all_strategies]
            assert id1 in ids
            assert id2 in ids
        finally:
            if sm._check_task is not None and not sm._check_task.done():
                sm._check_task.cancel()

    @pytest.mark.asyncio
    async def test_initialize_persisted_status(
        self, event_bus, db_manager, sample_strategy
    ):
        """Загруженные стратегии сохраняют статус из БД."""
        # Сохраняем стратегию со статусом WAITING
        sample_strategy.status = StrategyStatus.WAITING
        sample_strategy.start_time = datetime.now() + timedelta(days=1)
        strategy_id = await db_manager.save_strategy(sample_strategy)

        sm = StrategyManager(event_bus=event_bus, db_manager=db_manager, config={})
        await sm.initialize()

        try:
            loaded = sm.get_strategy(strategy_id)
            assert loaded is not None
            assert loaded.status == StrategyStatus.WAITING
        finally:
            if sm._check_task is not None and not sm._check_task.done():
                sm._check_task.cancel()

    # ──────────────────────────────────────────────
    # _check_start_times (проверка перехода WAITING → ACTIVE)
    # ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_check_start_times_transitions_to_active(
        self, strategy_manager, sample_strategy
    ):
        """_check_start_times переводит WAITING → ACTIVE когда start_time наступил."""
        # Создаём стратегию с start_time в прошлом
        sample_strategy.start_time = datetime.now() - timedelta(minutes=5)
        strategy_id = await strategy_manager.create_strategy(sample_strategy)

        # Вручную устанавливаем WAITING (как если бы start был в будущем при старте)
        strategy = strategy_manager.get_strategy(strategy_id)
        strategy.status = StrategyStatus.WAITING

        # Запускаем проверку вручную
        await strategy_manager._check_start_times_once()

        # Проверяем, что статус изменился на ACTIVE
        updated = strategy_manager.get_strategy(strategy_id)
        assert updated is not None
        assert updated.status == StrategyStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_check_start_times_ignores_future(
        self, strategy_manager, sample_strategy
    ):
        """_check_start_times не трогает WAITING стратегии с будущим start_time."""
        sample_strategy.start_time = datetime.now() + timedelta(days=1)
        strategy_id = await strategy_manager.create_strategy(sample_strategy)

        strategy = strategy_manager.get_strategy(strategy_id)
        strategy.status = StrategyStatus.WAITING

        await strategy_manager._check_start_times_once()

        updated = strategy_manager.get_strategy(strategy_id)
        assert updated is not None
        assert updated.status == StrategyStatus.WAITING  # не изменился

    @pytest.mark.asyncio
    async def test_check_start_times_without_start_time(
        self, strategy_manager, sample_strategy
    ):
        """WAITING без start_time не переводится в ACTIVE."""
        strategy_id = await strategy_manager.create_strategy(sample_strategy)

        strategy = strategy_manager.get_strategy(strategy_id)
        strategy.status = StrategyStatus.WAITING
        strategy.start_time = None  # нет времени старта

        await strategy_manager._check_start_times_once()

        updated = strategy_manager.get_strategy(strategy_id)
        assert updated is not None
        assert updated.status == StrategyStatus.WAITING

    # ──────────────────────────────────────────────
    # Проверка логирования и базовой функциональности
    # ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_create_strategy_preserves_legs(self, strategy_manager, sample_strategy):
        """Созданная стратегия сохраняет все ноги."""
        strategy_id = await strategy_manager.create_strategy(sample_strategy)
        loaded = strategy_manager.get_strategy(strategy_id)
        assert loaded is not None
        assert len(loaded.legs) == len(sample_strategy.legs)
        assert loaded.legs[0].option_type == sample_strategy.legs[0].option_type
        assert loaded.legs[0].strike == sample_strategy.legs[0].strike

    @pytest.mark.asyncio
    async def test_multiple_create_different_ids(self, strategy_manager, sample_strategy):
        """Последовательное создание даёт разные ID."""
        id1 = await strategy_manager.create_strategy(sample_strategy)
        id2 = await strategy_manager.create_strategy(sample_strategy)
        assert id2 > id1
