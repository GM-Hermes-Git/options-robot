"""Telegram-уведомления (заглушка для Этапа 1)."""

import logging

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Отправка уведомлений в Telegram (заглушка — Этап 6)."""

    def __init__(self, event_bus, config):
        self._event_bus = event_bus
        self._config = config
        self._enabled = config.get("telegram", {}).get("enabled", False)
        logger.info("TelegramNotifier: ЗАГЛУШКА (реализация — Этап 6)")
