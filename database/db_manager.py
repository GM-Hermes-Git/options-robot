"""Работа с SQLite (заглушка для Этапа 2)."""

import logging

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Менеджер базы данных SQLite (заглушка — Этап 2)."""

    def __init__(self, config):
        self._config = config
        self._db_path = config.get("database", {}).get("filename", "robot_data.db")
        logger.info("DatabaseManager: ЗАГЛУШКА (реализация — Этап 2)")
