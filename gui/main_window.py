"""
Главное окно приложения (PyQt6).

Реализует многопанельный интерфейс согласно ТЗ (раздел 9):
    - Вкладка «Подключение»: выбор провайдера, статус, Connect/Disconnect
    - Вкладка «Котировки»: таблица котировок выбранных инструментов
    - Заглушки для остальных вкладок (Этапы 2-6)

Тёмная тема, русский язык интерфейса.

Использование:
    app = QApplication(sys.argv)
    window = MainWindow(event_bus, config)
    window.show()
    sys.exit(app.exec())
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QPushButton,
    QStatusBar,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.event_bus import EventBus, EventType

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """
    Главное окно приложения Options Robot.

    Attributes:
        _event_bus: Шина событий для взаимодействия с модулями.
        _config: Конфигурация приложения.
        _provider: Активный провайдер данных (устанавливается извне).
        _order_provider: Активный провайдер ордеров (устанавливается извне).
    """

    def __init__(self, event_bus: EventBus, config: Dict[str, Any]):
        """
        Инициализация главного окна.

        Args:
            event_bus: Шина событий.
            config: Конфигурация приложения.
        """
        super().__init__()
        self._event_bus = event_bus
        self._config = config

        # Провайдеры (устанавливаются из main.py)
        self._provider = None       # MarketDataProvider
        self._order_provider = None # OrderProvider

        # Настройка окна
        self._setup_window()
        self._setup_ui()
        self._apply_theme()
        self._setup_status_bar()

        logger.info("Главное окно инициализировано")

    def _setup_window(self) -> None:
        """Настройка параметров окна."""
        gui_cfg = self._config.get("gui", {})
        title = gui_cfg.get("window_title", "Options Robot")
        width = gui_cfg.get("window_width", 1400)
        height = gui_cfg.get("window_height", 900)

        self.setWindowTitle(f"{title} v{self._config.get('app', {}).get('version', '0.1')}")
        self.resize(width, height)
        self.setMinimumSize(1024, 600)

    def _setup_ui(self) -> None:
        """Построение интерфейса."""
        # Центральный виджет с вкладками
        self._tabs = QTabWidget()
        self.setCentralWidget(self._tabs)

        # Создаём вкладки
        self._tab_connection = ConnectionTab(self._event_bus, self._config)
        self._tab_quotes = QuotesTab(self._config)
        self._tab_strategies = PlaceholderTab("Стратегии", "Управление стратегиями будет доступно на Этапе 2")
        self._tab_positions = PlaceholderTab("Позиции", "Монитор позиций будет доступен на Этапе 2")
        self._tab_orders = PlaceholderTab("Ордера", "Таблица активных ордеров будет доступна на Этапе 2")
        self._tab_log = PlaceholderTab("Лог", "Лог событий будет доступен на Этапе 1")
        self._tab_history = PlaceholderTab("История", "История сделок будет доступна на Этапе 5")
        self._tab_settings = PlaceholderTab("Настройки", "Настройки приложения")

        # Добавляем вкладки
        self._tabs.addTab(self._tab_connection, "🔌 Подключение")
        self._tabs.addTab(self._tab_quotes, "📊 Котировки")
        self._tabs.addTab(self._tab_strategies, "📋 Стратегии")
        self._tabs.addTab(self._tab_positions, "💼 Позиции")
        self._tabs.addTab(self._tab_orders, "📝 Ордера")
        self._tabs.addTab(self._tab_log, "📜 Лог")
        self._tabs.addTab(self._tab_history, "🕐 История")
        self._tabs.addTab(self._tab_settings, "⚙️ Настройки")

    def _apply_theme(self) -> None:
        """Применить тёмную тему оформления."""
        gui_cfg = self._config.get("gui", {})
        theme = gui_cfg.get("theme", "dark")

        if theme == "dark":
            dark_stylesheet = """
            QMainWindow {
                background-color: #1e1e2e;
                color: #cdd6f4;
            }
            QTabWidget::pane {
                border: 1px solid #313244;
                background-color: #1e1e2e;
            }
            QTabBar::tab {
                background-color: #181825;
                color: #6c7086;
                padding: 8px 16px;
                margin-right: 2px;
                border: 1px solid #313244;
                border-bottom: none;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }
            QTabBar::tab:selected {
                background-color: #1e1e2e;
                color: #cdd6f4;
                border-bottom: 2px solid #89b4fa;
            }
            QTabBar::tab:hover:!selected {
                color: #a6adc8;
            }
            QGroupBox {
                border: 1px solid #313244;
                border-radius: 6px;
                margin-top: 12px;
                padding-top: 16px;
                color: #cdd6f4;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 6px;
            }
            QPushButton {
                background-color: #45475a;
                color: #cdd6f4;
                border: 1px solid #585b70;
                border-radius: 4px;
                padding: 6px 16px;
                min-height: 28px;
            }
            QPushButton:hover {
                background-color: #585b70;
            }
            QPushButton:pressed {
                background-color: #313244;
            }
            QPushButton:disabled {
                background-color: #313244;
                color: #6c7086;
                border-color: #45475a;
            }
            QPushButton#connectBtn {
                background-color: #a6e3a1;
                color: #1e1e2e;
                font-weight: bold;
            }
            QPushButton#connectBtn:hover {
                background-color: #94e2d5;
            }
            QPushButton#disconnectBtn {
                background-color: #f38ba8;
                color: #1e1e2e;
                font-weight: bold;
            }
            QPushButton#disconnectBtn:hover {
                background-color: #eba0ac;
            }
            QComboBox {
                background-color: #313244;
                color: #cdd6f4;
                border: 1px solid #45475a;
                border-radius: 4px;
                padding: 4px 8px;
            }
            QComboBox::drop-down {
                border: none;
            }
            QComboBox QAbstractItemView {
                background-color: #313244;
                color: #cdd6f4;
                selection-background-color: #45475a;
            }
            QTableWidget {
                background-color: #181825;
                color: #cdd6f4;
                gridline-color: #313244;
                border: 1px solid #313244;
                border-radius: 4px;
            }
            QTableWidget::item {
                padding: 4px;
            }
            QTableWidget::item:selected {
                background-color: #45475a;
            }
            QHeaderView::section {
                background-color: #1e1e2e;
                color: #89b4fa;
                padding: 6px;
                border: 1px solid #313244;
                font-weight: bold;
            }
            QLabel {
                color: #cdd6f4;
            }
            QStatusBar {
                background-color: #181825;
                color: #6c7086;
                border-top: 1px solid #313244;
            }
            QLabel#statusConnected {
                color: #a6e3a1;
                font-weight: bold;
            }
            QLabel#statusDisconnected {
                color: #f38ba8;
                font-weight: bold;
            }
            """
            self.setStyleSheet(dark_stylesheet)

    def _setup_status_bar(self) -> None:
        """Настройка строки состояния."""
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)

        # Метка статуса подключения
        self._status_provider_label = QLabel("Провайдер: —")
        self._status_mode_label = QLabel("Режим: —")
        self._status_time_label = QLabel("")

        self._status_bar.addWidget(self._status_provider_label)
        self._status_bar.addWidget(self._status_mode_label)
        self._status_bar.addPermanentWidget(self._status_time_label)

        # Таймер обновления времени
        timer = QTimer(self)
        timer.timeout.connect(self._update_time)
        timer.start(1000)
        self._update_time()

    def _update_time(self) -> None:
        """Обновить время в статус-баре."""
        now = datetime.now().strftime("%H:%M:%S")
        self._status_time_label.setText(now)

    # ─────────────────────────────────────────────────────────────────
    # Публичные методы
    # ─────────────────────────────────────────────────────────────────

    def set_data_provider(self, provider) -> None:
        """
        Установить провайдер рыночных данных.

        Args:
            provider: Экземпляр MarketDataProvider.
        """
        self._provider = provider
        self._tab_connection.set_data_provider(provider)
        self._tab_quotes.set_data_provider(provider)

    def set_order_provider(self, order_provider) -> None:
        """
        Установить провайдер ордеров.

        Args:
            order_provider: Экземпляр OrderProvider.
        """
        self._order_provider = order_provider
        self._tab_connection.set_order_provider(order_provider)

    def update_provider_status(self, provider_name: str, connected: bool) -> None:
        """
        Обновить статус провайдера в статус-баре.

        Args:
            provider_name: Название провайдера.
            connected: True, если подключён.
        """
        status = "подключён" if connected else "отключён"
        self._status_provider_label.setText(f"Провайдер: {provider_name} ({status})")

    def update_mode_status(self, mode: str) -> None:
        """
        Обновить режим работы в статус-баре.

        Args:
            mode: Режим (moex_simulation, alor_demo, alor_production).
        """
        mode_names = {
            "moex_simulation": "MOEX + Симуляция",
            "alor_demo": "Alor Демо",
            "alor_production": "Alor Боевой",
        }
        self._status_mode_label.setText(f"Режим: {mode_names.get(mode, mode)}")


class ConnectionTab(QWidget):
    """Вкладка «Подключение»."""

    def __init__(self, event_bus: EventBus, config: Dict[str, Any]):
        super().__init__()
        self._event_bus = event_bus
        self._config = config
        self._provider = None
        self._order_provider = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        # ── Группа: Выбор провайдера ──
        provider_group = QGroupBox("Провайдер данных")
        provider_layout = QVBoxLayout(provider_group)

        # Строка выбора
        select_row = QHBoxLayout()
        select_row.addWidget(QLabel("Источник данных:"))
        self._provider_combo = QComboBox()
        self._provider_combo.addItem("MOEX ISS API", "moex")
        self._provider_combo.addItem("Alor API (Демо)", "alor_demo")
        self._provider_combo.addItem("Alor API (Боевой)", "alor_production")
        self._provider_combo.setCurrentIndex(0)  # MOEX по умолчанию
        select_row.addWidget(self._provider_combo)
        select_row.addStretch()
        provider_layout.addLayout(select_row)

        # Статус подключения
        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("Статус:"))
        self._status_label = QLabel("Отключён")
        self._status_label.setObjectName("statusDisconnected")
        status_row.addWidget(self._status_label)
        status_row.addStretch()
        provider_layout.addLayout(status_row)

        # Кнопки
        btn_row = QHBoxLayout()
        self._connect_btn = QPushButton("🟢 Подключиться")
        self._connect_btn.setObjectName("connectBtn")
        self._connect_btn.clicked.connect(self._on_connect)
        btn_row.addWidget(self._connect_btn)

        self._disconnect_btn = QPushButton("🔴 Отключиться")
        self._disconnect_btn.setObjectName("disconnectBtn")
        self._disconnect_btn.setEnabled(False)
        self._disconnect_btn.clicked.connect(self._on_disconnect)
        btn_row.addWidget(self._disconnect_btn)

        btn_row.addStretch()
        provider_layout.addLayout(btn_row)

        layout.addWidget(provider_group)

        # ── Группа: Информация ──
        info_group = QGroupBox("Информация о подключении")
        info_layout = QVBoxLayout(info_group)

        self._info_url = QLabel("URL: —")
        self._info_mode = QLabel("Режим: MOEX + Симуляция ордеров")
        self._info_status = QLabel("Состояние: Не подключено")

        info_layout.addWidget(self._info_url)
        info_layout.addWidget(self._info_mode)
        info_layout.addWidget(self._info_status)
        layout.addWidget(info_group)

        layout.addStretch()

    def set_data_provider(self, provider) -> None:
        """Установить провайдер данных."""
        self._provider = provider

    def set_order_provider(self, order_provider) -> None:
        """Установить провайдер ордеров."""
        self._order_provider = order_provider

    def _on_connect(self) -> None:
        """Обработчик нажатия «Подключиться»."""
        provider_type = self._provider_combo.currentData()

        if provider_type == "moex" and self._provider:
            # Используем EventBus для асинхронного подключения
            self._event_bus.publish_sync(
                EventType.PROVIDER_CONNECTED,
                {"provider": "moex", "action": "connect"},
                source="GUI",
            )
            self._status_label.setText("Подключается...")
            self._status_label.setObjectName("statusDisconnected")
            self._status_label.style().unpolish(self._status_label)
            self._status_label.style().polish(self._status_label)
            self._connect_btn.setEnabled(False)
        else:
            logger.warning("Режим %s пока не реализован", provider_type)

    def _on_disconnect(self) -> None:
        """Обработчик нажатия «Отключиться»."""
        self._event_bus.publish_sync(
            EventType.PROVIDER_DISCONNECTED,
            {"provider": "moex", "action": "disconnect"},
            source="GUI",
        )
        self._status_label.setText("Отключён")
        self._status_label.setObjectName("statusDisconnected")
        self._status_label.style().unpolish(self._status_label)
        self._status_label.style().polish(self._status_label)
        self._connect_btn.setEnabled(True)
        self._disconnect_btn.setEnabled(False)

    def update_connection_status(self, connected: bool, provider_name: str = "MOEX") -> None:
        """
        Обновить отображение статуса подключения.

        Args:
            connected: True, если подключён.
            provider_name: Название провайдера.
        """
        if connected:
            self._status_label.setText(f"Подключён ({provider_name})")
            self._status_label.setObjectName("statusConnected")
            self._connect_btn.setEnabled(False)
            self._disconnect_btn.setEnabled(True)
            self._info_status.setText("Состояние: Подключено ✓")
        else:
            self._status_label.setText("Отключён")
            self._status_label.setObjectName("statusDisconnected")
            self._connect_btn.setEnabled(True)
            self._disconnect_btn.setEnabled(False)
            self._info_status.setText("Состояние: Не подключено")

        # Принудительно обновляем стиль
        self._status_label.style().unpolish(self._status_label)
        self._status_label.style().polish(self._status_label)


class QuotesTab(QWidget):
    """Вкладка «Котировки»."""

    COLUMNS = ["Инструмент", "Bid", "Ask", "Last", "Mid", "Спред", "Спред %", "OI", "Обновлено"]

    def __init__(self, config: Dict[str, Any]):
        super().__init__()
        self._config = config
        self._provider = None
        self._quotes_data: Dict[str, Any] = {}
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Панель управления
        control_row = QHBoxLayout()

        control_row.addWidget(QLabel("Инструменты:"))
        self._instrument_input = QComboBox()
        self._instrument_input.setEditable(True)
        self._instrument_input.setMinimumWidth(300)
        self._instrument_input.setPlaceholderText("Введите тикер и нажмите Enter...")
        self._instrument_input.lineEdit().returnPressed.connect(self._add_instrument)
        control_row.addWidget(self._instrument_input)

        self._add_btn = QPushButton("➕ Добавить")
        self._add_btn.clicked.connect(self._add_instrument)
        control_row.addWidget(self._add_btn)

        self._refresh_btn = QPushButton("🔄 Обновить")
        self._refresh_btn.clicked.connect(self._refresh_quotes)
        control_row.addWidget(self._refresh_btn)

        self._clear_btn = QPushButton("🗑️ Очистить")
        self._clear_btn.clicked.connect(self._clear_quotes)
        control_row.addWidget(self._clear_btn)

        control_row.addStretch()
        layout.addLayout(control_row)

        # Таблица котировок
        self._table = QTableWidget()
        self._table.setColumnCount(len(self.COLUMNS))
        self._table.setHorizontalHeaderLabels(self.COLUMNS)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        # Растягиваем колонки
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)  # Инструмент
        for col in range(1, len(self.COLUMNS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)

        layout.addWidget(self._table)

        # Информационная строка
        self._info_label = QLabel("Готово к работе. Добавьте инструменты для отслеживания.")
        layout.addWidget(self._info_label)

    def set_data_provider(self, provider) -> None:
        """Установить провайдер данных."""
        self._provider = provider

    def _add_instrument(self) -> None:
        """Добавить инструмент в таблицу котировок."""
        ticker = self._instrument_input.currentText().strip()
        if not ticker:
            return

        # Нормализуем тикер (убираем лишние пробелы, приводим к верхнему регистру)
        ticker = ticker.upper()

        if ticker in self._quotes_data:
            logger.debug("Инструмент %s уже в таблице", ticker)
            return

        # Добавляем строку в таблицу
        row = self._table.rowCount()
        self._table.insertRow(row)

        # Заполняем инструмент
        item = QTableWidgetItem(ticker)
        item.setForeground(QColor("#89b4fa"))
        self._table.setItem(row, 0, item)

        # Остальные ячейки заполняем прочерками
        for col in range(1, len(self.COLUMNS)):
            self._table.setItem(row, col, QTableWidgetItem("—"))

        self._quotes_data[ticker] = {"row": row}
        self._instrument_input.clearFocus()
        self._instrument_input.setCurrentText("")

        logger.info("Добавлен инструмент в таблицу котировок: %s", ticker)

    def _refresh_quotes(self) -> None:
        """Обновить котировки (запускает асинхронный запрос)."""
        if not self._provider:
            self._info_label.setText("⚠️ Провайдер данных не подключён")
            return

        # В Этапе 1 обновление через поллинг-таймер в main.py
        # Здесь — ручной запрос
        self._info_label.setText("Запрос котировок...")

        # Будет доработано: вызов self._provider.get_quotes() через asyncio
        logger.info("Запрос обновления котировок")

    def _clear_quotes(self) -> None:
        """Очистить таблицу котировок."""
        self._table.setRowCount(0)
        self._quotes_data.clear()
        self._info_label.setText("Таблица очищена.")
        logger.info("Таблица котировок очищена")

    def update_quote(self, ticker: str, bid: float, ask: float,
                     last: float, oi: int = 0) -> None:
        """
        Обновить строку котировки в таблице.

        Args:
            ticker: Тикер инструмента.
            bid: Цена покупки.
            ask: Цена продажи.
            last: Цена последней сделки.
            oi: Открытый интерес.
        """
        if ticker not in self._quotes_data:
            return

        row = self._quotes_data[ticker]["row"]
        mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else (last or 0)
        spread = ask - bid if (bid > 0 and ask > 0) else 0
        spread_pct = (spread / mid * 100) if mid > 0 else 0

        values = [
            bid if bid > 0 else "—",
            ask if ask > 0 else "—",
            last if last > 0 else "—",
            f"{mid:.2f}" if mid > 0 else "—",
            f"{spread:.2f}" if spread > 0 else "—",
            f"{spread_pct:.2f}%" if spread_pct > 0 else "—",
            oi if oi > 0 else "—",
            datetime.now().strftime("%H:%M:%S"),
        ]

        for col, value in enumerate(values, start=1):
            item = QTableWidgetItem(str(value))
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(row, col, item)


class PlaceholderTab(QWidget):
    """Вкладка-заглушка для ещё не реализованных разделов."""

    def __init__(self, title: str, description: str):
        """
        Инициализация заглушки.

        Args:
            title: Заголовок вкладки.
            description: Описание (когда будет доступно).
        """
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title_label = QLabel(f"📋 {title}")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)

        desc_label = QLabel(description)
        desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc_label.setStyleSheet("color: #6c7086; font-size: 12px;")
        layout.addWidget(desc_label)

        version_label = QLabel("В разработке")
        version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version_label.setStyleSheet("color: #45475a; font-size: 11px; margin-top: 20px;")
        layout.addWidget(version_label)
