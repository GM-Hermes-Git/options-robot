"""
Вкладка «Лог» — лог событий системы.

Заменяет PlaceholderTab("Лог", ...) в MainWindow.
Подписывается на ВСЕ события EventBus и отображает их в реальном времени.
"""

import logging
from datetime import datetime
from typing import List, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QTextCursor
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.event_bus import EventBus, EventType, Event

logger = logging.getLogger(__name__)

# Цвета для разных типов событий
EVENT_COLORS = {
    # Торговые события
    EventType.TRIGGER_FIRED: QColor("#f9e2af"),        # жёлтый
    EventType.TRIGGER_DEACTIVATED: QColor("#6c7086"),  # серый
    EventType.ORDER_PLACED: QColor("#89b4fa"),         # синий
    EventType.ORDER_FILLED: QColor("#a6e3a1"),         # зелёный
    EventType.ORDER_PARTIAL_FILL: QColor("#fab387"),   # оранжевый
    EventType.ORDER_CANCELLED: QColor("#f38ba8"),      # красный
    EventType.POSITION_UPDATED: QColor("#94e2d5"),     # бирюзовый
    EventType.STOP_LOSS_TRIGGERED: QColor("#f38ba8"),  # красный
    EventType.TAKE_PROFIT_TRIGGERED: QColor("#a6e3a1"),# зелёный
    EventType.STRATEGY_STOPPED: QColor("#f38ba8"),     # красный

    # Системные и ошибочные
    EventType.CONNECTION_ERROR: QColor("#f38ba8"),     # красный
    EventType.CONNECTION_RESTORED: QColor("#a6e3a1"),  # зелёный
    EventType.CRITICAL_ERROR: QColor("#f38ba8"),       # красный
    EventType.MAX_CONTRACTS_WARNING: QColor("#fab387"),# оранжевый

    # Системные
    EventType.PROVIDER_CONNECTED: QColor("#a6e3a1"),   # зелёный
    EventType.PROVIDER_DISCONNECTED: QColor("#f38ba8"),# красный
    EventType.QUOTE_UPDATED: QColor("#6c7086"),        # серый
}

# Значок для типа события
EVENT_ICONS = {
    EventType.TRIGGER_FIRED: "🔥",
    EventType.TRIGGER_DEACTIVATED: "⏸",
    EventType.ORDER_PLACED: "📋",
    EventType.ORDER_FILLED: "✅",
    EventType.ORDER_PARTIAL_FILL: "⏳",
    EventType.ORDER_CANCELLED: "❌",
    EventType.POSITION_UPDATED: "💼",
    EventType.HEDGE_REQUIRED: "🛡",
    EventType.HEDGE_EXECUTED: "🛡",
    EventType.STOP_LOSS_TRIGGERED: "🛑",
    EventType.TAKE_PROFIT_TRIGGERED: "💰",
    EventType.POSITION_CLOSED: "🔒",
    EventType.MAX_CONTRACTS_WARNING: "⚠️",
    EventType.STRATEGY_STOPPED: "⏹",
    EventType.CONNECTION_ERROR: "🔴",
    EventType.CONNECTION_RESTORED: "🟢",
    EventType.CRITICAL_ERROR: "🚨",
    EventType.PROVIDER_CONNECTED: "🟢",
    EventType.PROVIDER_DISCONNECTED: "🔴",
}


class LogTab(QWidget):
    """
    Вкладка «Лог» — отображение событий системы в реальном времени.

    Подписывается на все типы событий EventBus и отображает их
    в моноширинном текстовом поле с цветовой кодировкой.

    Attributes:
        _event_bus: Шина событий для подписки.
        _max_lines: Максимальное количество строк в логе.
        _message_queue: Очередь сообщений для потокобезопасного GUI-обновления.
    """

    # Максимальное количество строк в логе
    MAX_LINES = 10000

    def __init__(self, event_bus: EventBus):
        """
        Инициализация вкладки лога.

        Args:
            event_bus: Шина событий.
        """
        super().__init__()
        self._event_bus = event_bus
        self._max_lines = self.MAX_LINES

        # Очередь сообщений для безопасного обновления GUI
        self._message_queue: List[str] = []

        self._setup_ui()
        self._subscribe_all_events()

        # Таймер для периодической обработки очереди сообщений
        self._flush_timer = QTimer(self)
        self._flush_timer.timeout.connect(self._flush_message_queue)
        self._flush_timer.start(100)  # каждые 100 мс

        logger.info("LogTab инициализирована")

    def _setup_ui(self) -> None:
        """Построение интерфейса вкладки."""
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # ── Панель управления ──
        toolbar = QHBoxLayout()

        self._btn_clear = QPushButton("🗑 Очистить")
        self._btn_clear.clicked.connect(self._clear_log)
        toolbar.addWidget(self._btn_clear)

        self._btn_auto_scroll = QPushButton("🔽 Автопрокрутка")
        self._btn_auto_scroll.setCheckable(True)
        self._btn_auto_scroll.setChecked(True)
        toolbar.addWidget(self._btn_auto_scroll)

        toolbar.addStretch()

        self._entries_label = QLabel("Записей: 0")
        toolbar.addWidget(self._entries_label)

        layout.addLayout(toolbar)

        # ── Текстовый лог ──
        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)

        # Моноширинный шрифт
        log_font = QFont("Consolas", 9)
        log_font.setStyleHint(QFont.StyleHint.Monospace)
        self._log_view.setFont(log_font)

        # Фон и цвета
        self._log_view.setStyleSheet("""
            QTextEdit {
                background-color: #11111b;
                color: #cdd6f4;
                border: 1px solid #313244;
                border-radius: 4px;
                padding: 4px;
            }
        """)

        layout.addWidget(self._log_view)

    def _subscribe_all_events(self) -> None:
        """Подписаться на ВСЕ типы событий EventBus."""
        self._sub_ids = []

        for event_type in EventType:
            try:
                sub_id = self._event_bus.subscribe(
                    event_type,
                    self._on_any_event,
                    priority=100,  # самый низкий приоритет — логируем после всех
                )
                self._sub_ids.append((event_type, sub_id))
            except Exception as exc:
                logger.warning(
                    "Не удалось подписаться на %s: %s",
                    event_type.name, exc,
                )

        logger.debug(
            "LogTab подписан на %d типов событий", len(self._sub_ids)
        )

    async def _on_any_event(self, event: Event) -> None:
        """
        Асинхронный обработчик любого события — добавляет строку в очередь.

        Args:
            event: Объект события.
        """
        timestamp = datetime.fromtimestamp(event.timestamp)
        time_str = timestamp.strftime("%H:%M:%S")

        event_name = event.type.name
        source = event.source if event.source else "—"
        icon = EVENT_ICONS.get(event.type, "•")
        data_str = str(event.data) if event.data else ""

        # Форматируем строку
        log_line = f"[{time_str}] [{event_name}] {icon} {source}: {data_str}"

        # Добавляем в очередь (потокобезопасно)
        self._message_queue.append(log_line)

    def _flush_message_queue(self) -> None:
        """Обработать очередь сообщений и обновить GUI (вызывается в Qt-потоке)."""
        if not self._message_queue:
            return

        # Берём все накопленные сообщения
        messages = self._message_queue.copy()
        self._message_queue.clear()

        # Определяем, нужно ли прокручивать
        scrollbar = self._log_view.verticalScrollBar()
        at_bottom = scrollbar is None or scrollbar.value() >= scrollbar.maximum() - 20

        for log_line in messages:
            # Добавляем строку с цветом
            self._append_log_line(log_line)

        # Автопрокрутка, если включена
        if at_bottom and self._btn_auto_scroll.isChecked():
            cursor = self._log_view.textCursor()
            cursor.movePosition(QTextCursor.MovementOperation.End)
            self._log_view.setTextCursor(cursor)
            self._log_view.ensureCursorVisible()

        # Обновляем счётчик
        doc = self._log_view.document()
        line_count = doc.blockCount() if doc else 0
        self._entries_label.setText(f"Записей: {line_count - 1}")

    def _append_log_line(self, log_line: str) -> None:
        """
        Добавить одну строку в лог с цветовым форматированием.

        Args:
            log_line: Форматированная строка лога.
        """
        # Извлекаем тип события из строки
        # Формат: [HH:MM:SS] [EVENT_TYPE] icon source: data
        event_type_name = ""
        if "][" in log_line:
            parts = log_line.split("][")
            if len(parts) >= 2:
                event_type_name = parts[1].rstrip("]")

        # Определяем цвет
        try:
            event_type = EventType[event_type_name]
            color = EVENT_COLORS.get(event_type, QColor("#6c7086"))
        except (KeyError, ValueError):
            color = QColor("#6c7086")

        # Собираем HTML-строку с цветом
        color_hex = color.name()

        # Ограничиваем количество строк
        doc = self._log_view.document()
        if doc and doc.blockCount() > self._max_lines:
            # Удаляем первую четверть строк для поддержания производительности
            cursor = self._log_view.textCursor()
            cursor.movePosition(QTextCursor.MovementOperation.Start)
            cursor.movePosition(
                QTextCursor.MovementOperation.Down,
                QTextCursor.MoveMode.KeepAnchor,
                self._max_lines // 4,
            )
            cursor.removeSelectedText()
            cursor.deleteChar()  # удаляем завершающий перевод строки

        # Вставляем цветную строку
        html_line = f'<p style="color: {color_hex}; margin: 0; padding: 0;">{self._html_escape(log_line)}</p>'
        self._log_view.insertHtml(html_line)

    def _html_escape(self, text: str) -> str:
        """
        Экранировать HTML-символы в тексте.

        Args:
            text: Исходный текст.

        Returns:
            Текст с экранированными HTML-символами.
        """
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#039;")
        )

    def _clear_log(self) -> None:
        """Очистить лог событий."""
        self._log_view.clear()
        self._message_queue.clear()
        self._entries_label.setText("Записей: 0")
        logger.debug("Лог очищен пользователем")
