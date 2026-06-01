"""Тесты для DatabaseManager (SQLite)."""

import json
import os
import tempfile
from datetime import datetime

import pytest

from core.strategy_manager import Leg, StrategyDefinition, StrategyStatus
from core.providers.market_data import OptionType
from database.db_manager import DatabaseManager


@pytest.fixture
async def db():
    """Создать DatabaseManager с временной БД."""
    manager = DatabaseManager(":memory:")
    await manager.initialize()
    yield manager
    await manager.close()


@pytest.fixture
def sample_strategy():
    """Создать тестовую стратегию."""
    legs = [
        Leg(leg_index=0, option_type=OptionType.CALL, strike=85000.0, sign=1, quantity=1),
        Leg(leg_index=1, option_type=OptionType.PUT, strike=82000.0, sign=1, quantity=1),
    ]
    return StrategyDefinition(
        name="Si straddle",
        base_asset="Si",
        legs=legs,
        trigger_level=84000.0,
        trigger_deactivation_threshold=200.0,
        max_contracts_per_leg=5,
        sl_percent=50.0,
        tp_percent=100.0,
    )


class TestDatabaseManager:
    """Тесты для DatabaseManager."""

    @pytest.mark.asyncio
    async def test_initialize_creates_table(self, db):
        """Инициализация создаёт таблицу strategies."""
        async with db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='strategies'"
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None
        assert row[0] == "strategies"

    @pytest.mark.asyncio
    async def test_initialize_idempotent(self, db):
        """Повторная инициализация не вызывает ошибку."""
        # Первая инициализация уже сделана в фикстуре
        await db.initialize()  # Вторая инициализация
        # Проверяем, что таблица существует
        async with db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='strategies'"
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None

    @pytest.mark.asyncio
    async def test_table_has_expected_columns(self, db):
        """Таблица strategies содержит все ожидаемые колонки."""
        async with db._conn.execute("PRAGMA table_info('strategies')") as cursor:
            columns = {row[1]: row[2] for row in await cursor.fetchall()}

        expected_columns = {
            "strategy_id": "INTEGER",
            "name": "TEXT",
            "base_asset": "TEXT",
            "status": "TEXT",
            "legs_json": "TEXT",
            "trigger_level": "REAL",
            "trigger_deactivation_threshold": "REAL",
            "start_time": "TEXT",
            "end_time": "TEXT",
            "max_contracts_per_leg": "INTEGER",
            "sl_percent": "REAL",
            "tp_percent": "REAL",
            "created_at": "TEXT",
            "updated_at": "TEXT",
        }

        for col_name, col_type in expected_columns.items():
            assert col_name in columns, f"Column {col_name} not found"
            assert col_type in columns[col_name].upper(), \
                f"Column {col_name} type mismatch: expected {col_type}, got {columns[col_name]}"

    @pytest.mark.asyncio
    async def test_strategy_id_is_autoincrement(self, db):
        """strategy_id должен быть PRIMARY KEY AUTOINCREMENT."""
        async with db._conn.execute("PRAGMA table_info('strategies')") as cursor:
            columns = await cursor.fetchall()
        pk_col = next(col for col in columns if col[5] == 1)  # 5 = pk flag
        assert pk_col[1] == "strategy_id"

    @pytest.mark.asyncio
    async def test_save_new_strategy(self, db, sample_strategy):
        """Сохранение новой стратегии возвращает ID > 0."""
        strategy_id = await db.save_strategy(sample_strategy)
        assert strategy_id > 0

    @pytest.mark.asyncio
    async def test_save_and_load_strategy(self, db, sample_strategy):
        """Сохранённую стратегию можно загрузить."""
        strategy_id = await db.save_strategy(sample_strategy)
        loaded = await db.load_strategy(strategy_id)

        assert loaded is not None
        assert loaded.strategy_id == strategy_id
        assert loaded.name == sample_strategy.name
        assert loaded.base_asset == sample_strategy.base_asset
        assert loaded.trigger_level == sample_strategy.trigger_level
        assert loaded.trigger_deactivation_threshold == sample_strategy.trigger_deactivation_threshold
        assert loaded.max_contracts_per_leg == sample_strategy.max_contracts_per_leg
        assert loaded.sl_percent == sample_strategy.sl_percent
        assert loaded.tp_percent == sample_strategy.tp_percent
        assert loaded.status == StrategyStatus.CONFIGURED
        assert len(loaded.legs) == 2
        assert loaded.legs[0].option_type == OptionType.CALL
        assert loaded.legs[0].strike == 85000.0

    @pytest.mark.asyncio
    async def test_save_multiple_strategies(self, db, sample_strategy):
        """Сохранение нескольких стратегий даёт разные ID."""
        id1 = await db.save_strategy(sample_strategy)
        id2 = await db.save_strategy(sample_strategy)
        assert id1 != id2
        assert id2 > id1

    @pytest.mark.asyncio
    async def test_update_existing_strategy(self, db, sample_strategy):
        """Обновление существующей стратегии."""
        strategy_id = await db.save_strategy(sample_strategy)

        # Меняем параметры
        sample_strategy.strategy_id = strategy_id
        sample_strategy.name = "Updated straddle"
        sample_strategy.trigger_level = 85000.0
        sample_strategy.status = StrategyStatus.ACTIVE

        updated_id = await db.save_strategy(sample_strategy)
        assert updated_id == strategy_id

        loaded = await db.load_strategy(strategy_id)
        assert loaded.name == "Updated straddle"
        assert loaded.trigger_level == 85000.0
        assert loaded.status == StrategyStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_load_nonexistent_strategy(self, db):
        """Загрузка несуществующей стратегии возвращает None."""
        result = await db.load_strategy(999)
        assert result is None

    @pytest.mark.asyncio
    async def test_load_all_strategies_empty(self, db):
        """Загрузка всех стратегий из пустой БД."""
        strategies = await db.load_all_strategies()
        assert strategies == []

    @pytest.mark.asyncio
    async def test_load_all_strategies(self, db, sample_strategy):
        """Загрузка всех стратегий."""
        id1 = await db.save_strategy(sample_strategy)

        # Создаём вторую стратегию
        legs2 = [
            Leg(leg_index=0, option_type=OptionType.CALL, strike=90000.0, sign=-1, quantity=2),
        ]
        strat2 = StrategyDefinition(
            name="Si call credit spread",
            base_asset="Si",
            legs=legs2,
            trigger_level=88000.0,
        )
        id2 = await db.save_strategy(strat2)

        strategies = await db.load_all_strategies()
        assert len(strategies) == 2

        ids = [s.strategy_id for s in strategies]
        assert id1 in ids
        assert id2 in ids

    @pytest.mark.asyncio
    async def test_delete_strategy(self, db, sample_strategy):
        """Удаление стратегии."""
        strategy_id = await db.save_strategy(sample_strategy)
        result = await db.delete_strategy(strategy_id)
        assert result is True

        loaded = await db.load_strategy(strategy_id)
        assert loaded is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_strategy(self, db):
        """Удаление несуществующей стратегии возвращает False."""
        result = await db.delete_strategy(999)
        assert result is False

    @pytest.mark.asyncio
    async def test_strategy_with_all_fields(self, db):
        """Стратегия со всеми полями сохраняется и загружается корректно."""
        legs = [
            Leg(
                leg_index=0,
                option_type=OptionType.CALL,
                strike=100000.0,
                sign=-1,
                quantity=3,
                iv_mode="manual",
                manual_iv=35.0,
                iv_multiplier=0.9,
            ),
        ]
        strat = StrategyDefinition(
            name="Full test",
            base_asset="Eu",
            status=StrategyStatus.WAITING,
            legs=legs,
            trigger_level=95000.0,
            trigger_deactivation_threshold=500.0,
            start_time=datetime(2025, 7, 1, 10, 0, 0),
            end_time=datetime(2025, 7, 31, 18, 45, 0),
            max_contracts_per_leg=10,
            sl_percent=30.0,
            tp_percent=200.0,
        )
        strategy_id = await db.save_strategy(strat)
        loaded = await db.load_strategy(strategy_id)

        assert loaded.name == "Full test"
        assert loaded.base_asset == "Eu"
        assert loaded.status == StrategyStatus.WAITING
        assert loaded.trigger_level == 95000.0
        assert loaded.trigger_deactivation_threshold == 500.0
        assert loaded.start_time == datetime(2025, 7, 1, 10, 0, 0)
        assert loaded.end_time == datetime(2025, 7, 31, 18, 45, 0)
        assert loaded.max_contracts_per_leg == 10
        assert loaded.sl_percent == 30.0
        assert loaded.tp_percent == 200.0
        assert loaded.legs[0].iv_mode == "manual"
        assert loaded.legs[0].manual_iv == 35.0
        assert loaded.legs[0].iv_multiplier == 0.9

    @pytest.mark.asyncio
    async def test_strategy_with_optional_times_none(self, db):
        """Стратегия с start_time=None и end_time=None."""
        legs = [
            Leg(leg_index=0, option_type=OptionType.CALL, strike=85000.0, sign=1, quantity=1),
        ]
        strat = StrategyDefinition(
            name="No times",
            base_asset="Si",
            legs=legs,
            trigger_level=84000.0,
        )
        strategy_id = await db.save_strategy(strat)
        loaded = await db.load_strategy(strategy_id)

        assert loaded.start_time is None
        assert loaded.end_time is None

    @pytest.mark.asyncio
    async def test_close_connection(self, db):
        """close() закрывает соединение."""
        await db.close()
        with pytest.raises(Exception):
            await db.save_strategy(
                StrategyDefinition(
                    name="test",
                    base_asset="Si",
                    legs=[Leg(leg_index=0, option_type=OptionType.CALL, strike=85000.0, sign=1, quantity=1)],
                    trigger_level=84000.0,
                )
            )

    @pytest.mark.asyncio
    async def test_file_based_database(self):
        """Тест с файловой БД (временный файл)."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            db = DatabaseManager(db_path)
            await db.initialize()

            legs = [
                Leg(leg_index=0, option_type=OptionType.CALL, strike=85000.0, sign=1, quantity=1),
            ]
            strat = StrategyDefinition(
                name="File test",
                base_asset="Si",
                legs=legs,
                trigger_level=84000.0,
            )
            strategy_id = await db.save_strategy(strat)
            assert strategy_id > 0

            loaded = await db.load_strategy(strategy_id)
            assert loaded.name == "File test"

            await db.close()

            # Проверяем, что файл существует и не пустой
            assert os.path.exists(db_path)
            assert os.path.getsize(db_path) > 0
        finally:
            if os.path.exists(db_path):
                os.unlink(db_path)
