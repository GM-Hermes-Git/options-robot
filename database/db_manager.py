"""Асинхронный менеджер SQLite для хранения стратегий."""

import json
import logging
from datetime import datetime
from typing import List, Optional

import aiosqlite

from core.strategy_manager import Leg, StrategyDefinition, StrategyStatus

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Асинхронный менеджер SQLite.

    Отвечает за инициализацию БД, CRUD операции над стратегиями.
    """

    def __init__(self, db_path: str) -> None:
        """
        Args:
            db_path: Путь к файлу БД (например, ":memory:" или "robot_data.db").
        """
        self._db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        """Создать/открыть БД и создать таблицу strategies, если её нет."""
        if self._conn is None:
            self._conn = await aiosqlite.connect(self._db_path)

        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")

        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS strategies (
                strategy_id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                name                        TEXT    NOT NULL,
                base_asset                  TEXT    NOT NULL,
                status                      TEXT    NOT NULL DEFAULT 'CONFIGURED',
                legs_json                   TEXT    NOT NULL DEFAULT '[]',
                trigger_level               REAL    NOT NULL,
                trigger_deactivation_threshold REAL NOT NULL DEFAULT 0.0,
                start_time                  TEXT,
                end_time                    TEXT,
                max_contracts_per_leg       INTEGER NOT NULL DEFAULT 1,
                sl_percent                  REAL    NOT NULL DEFAULT 50.0,
                tp_percent                  REAL    NOT NULL DEFAULT 100.0,
                created_at                  TEXT    NOT NULL,
                updated_at                  TEXT    NOT NULL
            )
            """
        )
        await self._conn.commit()
        logger.info("Database initialized at %s", self._db_path)

    async def save_strategy(self, strategy: StrategyDefinition) -> int:
        """Сохранить или обновить стратегию.

        Если strategy.strategy_id == 0 — вставка новой записи.
        Иначе — обновление существующей (UPSERT).

        Returns:
            strategy_id.
        """
        if self._conn is None:
            raise RuntimeError("DatabaseManager not initialized. Call initialize() first.")

        legs_json = StrategyDefinition.to_legs_json(strategy.legs)
        created_at_iso = strategy.created_at.isoformat()
        updated_at_iso = strategy.updated_at.isoformat()
        start_time_iso = strategy.start_time.isoformat() if strategy.start_time else None
        end_time_iso = strategy.end_time.isoformat() if strategy.end_time else None

        if strategy.strategy_id == 0:
            # Новая стратегия
            cursor = await self._conn.execute(
                """
                INSERT INTO strategies (
                    name, base_asset, status, legs_json,
                    trigger_level, trigger_deactivation_threshold,
                    start_time, end_time,
                    max_contracts_per_leg, sl_percent, tp_percent,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    strategy.name,
                    strategy.base_asset,
                    strategy.status.value,
                    legs_json,
                    strategy.trigger_level,
                    strategy.trigger_deactivation_threshold,
                    start_time_iso,
                    end_time_iso,
                    strategy.max_contracts_per_leg,
                    strategy.sl_percent,
                    strategy.tp_percent,
                    created_at_iso,
                    updated_at_iso,
                ),
            )
            strategy_id = cursor.lastrowid
        else:
            # Обновление существующей
            await self._conn.execute(
                """
                UPDATE strategies SET
                    name = ?, base_asset = ?, status = ?, legs_json = ?,
                    trigger_level = ?, trigger_deactivation_threshold = ?,
                    start_time = ?, end_time = ?,
                    max_contracts_per_leg = ?, sl_percent = ?, tp_percent = ?,
                    updated_at = ?
                WHERE strategy_id = ?
                """,
                (
                    strategy.name,
                    strategy.base_asset,
                    strategy.status.value,
                    legs_json,
                    strategy.trigger_level,
                    strategy.trigger_deactivation_threshold,
                    start_time_iso,
                    end_time_iso,
                    strategy.max_contracts_per_leg,
                    strategy.sl_percent,
                    strategy.tp_percent,
                    updated_at_iso,
                    strategy.strategy_id,
                ),
            )
            strategy_id = strategy.strategy_id

        await self._conn.commit()
        return strategy_id

    async def load_strategy(self, strategy_id: int) -> Optional[StrategyDefinition]:
        """Загрузить стратегию по ID.

        Returns:
            StrategyDefinition или None, если стратегия не найдена.
        """
        if self._conn is None:
            raise RuntimeError("DatabaseManager not initialized. Call initialize() first.")

        cursor = await self._conn.execute(
            "SELECT * FROM strategies WHERE strategy_id = ?",
            (strategy_id,),
        )
        row = await cursor.fetchone()

        if row is None:
            return None

        return self._row_to_strategy(row)

    async def load_all_strategies(self) -> List[StrategyDefinition]:
        """Загрузить все стратегии.

        Returns:
            Список StrategyDefinition (может быть пустым).
        """
        if self._conn is None:
            raise RuntimeError("DatabaseManager not initialized. Call initialize() first.")

        cursor = await self._conn.execute(
            "SELECT * FROM strategies ORDER BY strategy_id"
        )
        rows = await cursor.fetchall()
        return [self._row_to_strategy(row) for row in rows]

    async def delete_strategy(self, strategy_id: int) -> bool:
        """Удалить стратегию по ID.

        Returns:
            True, если запись была удалена; False, если не найдена.
        """
        if self._conn is None:
            raise RuntimeError("DatabaseManager not initialized. Call initialize() first.")

        cursor = await self._conn.execute(
            "DELETE FROM strategies WHERE strategy_id = ?",
            (strategy_id,),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def close(self) -> None:
        """Закрыть соединение с БД."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            logger.info("Database connection closed.")

    def _row_to_strategy(self, row: tuple) -> StrategyDefinition:
        """Преобразовать строку из БД в объект StrategyDefinition."""
        return StrategyDefinition(
            strategy_id=row[0],
            name=row[1],
            base_asset=row[2],
            status=StrategyStatus(row[3]),
            legs=StrategyDefinition.from_legs_json(row[4]),
            trigger_level=row[5],
            trigger_deactivation_threshold=row[6],
            start_time=datetime.fromisoformat(row[7]) if row[7] else None,
            end_time=datetime.fromisoformat(row[8]) if row[8] else None,
            max_contracts_per_leg=row[9],
            sl_percent=row[10],
            tp_percent=row[11],
            created_at=datetime.fromisoformat(row[12]),
            updated_at=datetime.fromisoformat(row[13]),
        )
