"""
Модуль логирования.

Настраивает корневой логгер приложения с ротацией файлов и выводом в консоль.
Формат: [TIMESTAMP] [LEVEL] [MODULE] Сообщение

Использование:
    from utils.logger import setup_logging, get_logger
    setup_logging(config)
    logger = get_logger(__name__)
    logger.info("Сообщение")
"""

import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Optional, Dict, Any


# Глобальный словарь для хранения настроек логирования
_log_config: Dict[str, Any] = {}
_initialized: bool = False


def setup_logging(config: Dict[str, Any]) -> None:
    """
    Инициализация системы логирования.

    Args:
        config: Полная конфигурация приложения (словарь из settings.json).
                Ожидается секция config['logging'] с ключами:
                - level: уровень логирования (DEBUG/INFO/WARNING/ERROR/CRITICAL)
                - log_dir: директория для файлов лога
                - max_file_size_mb: максимальный размер файла лога в МБ
                - backup_count: количество хранимых файлов лога
                - format: формат сообщения
                - date_format: формат даты/времени
    """
    global _log_config, _initialized

    log_cfg = config.get("logging", {})
    _log_config = log_cfg

    # Определяем уровень логирования
    level_name = log_cfg.get("level", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    # Создаём директорию для логов, если её нет
    log_dir = Path(log_cfg.get("log_dir", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)

    # Формат сообщений
    fmt = log_cfg.get(
        "format",
        "[%(asctime)s] [%(levelname)-8s] [%(name)s] %(message)s",
    )
    date_fmt = log_cfg.get("date_format", "%Y-%m-%d %H:%M:%S")
    formatter = logging.Formatter(fmt, datefmt=date_fmt)

    # Получаем корневой логгер
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Очищаем существующие обработчики (на случай повторного вызова)
    root_logger.handlers.clear()

    # Обработчик: файл с ротацией
    log_filename = log_dir / "robot.log"
    max_bytes = log_cfg.get("max_file_size_mb", 10) * 1024 * 1024
    backup_count = log_cfg.get("backup_count", 30)

    file_handler = logging.handlers.RotatingFileHandler(
        filename=str(log_filename),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # Обработчик: консоль (только для разработки)
    if not getattr(sys, 'frozen', False):  # Не выводим в консоль в собранном .exe
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    _initialized = True

    # Сообщаем о старте
    root_logger.info("=" * 60)
    root_logger.info("Логирование инициализировано. Уровень: %s", level_name)
    root_logger.info("Файл лога: %s", log_filename.absolute())


def get_logger(name: str) -> logging.Logger:
    """
    Получить логгер для указанного модуля.

    Args:
        name: Имя логгера (обычно __name__ модуля).

    Returns:
        Настроенный экземпляр logging.Logger.
    """
    return logging.getLogger(name)


def is_initialized() -> bool:
    """Проверить, инициализировано ли логирование."""
    return _initialized
