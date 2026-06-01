"""
Вкладка «Ордера» — таблица активных ордеров.

Заменяет PlaceholderTab("Ордера", ...) в MainWindow.
Отображает список ордеров из OrderProvider с цветовой индикацией
направления и статуса.
"""

import logging
from datetime import datetime
from typing import Any, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.event_bus import EventBus, EventType
from core.providers.market_data import OrderProvider, OrderInfo, OrderStatus, OrderSide

logger = logging.getLogger(__name__)

# Цвета для направления ордера
SIDE_COLORS = {
    OrderSide.BUY: QColor("#a6e3a1"),   # зелёный
    OrderSide.SELL: QColor("#f38ba8"),   # красный
}

# Цвета для статусов
STATUS_COLORS = {
    OrderStatus.PENDING: QColor("#f9e2af"),          # жёлтый
    OrderStatus.ACTIVE: QColor("#89b4fa"),           # синий
    OrderStatus.PARTIALLY_FILLED: QColor("#fab387"), # оранжевый
    OrderStatus.FILLED: QColor("#a6e3a1"),           # зелёный
    OrderStatus.CANCELLED: QColor("#6c7086"),        # серый
    OrderStatus.REJECTED: QColor("#f38ba8"),         # красный
}

STATUS_NAMES = {
    OrderStatus.PENDING: "Ожидает",
    OrderStatus.ACTIVE: "Активен",
    OrderStatus.PARTIALLY_FILLED: "Частично исполнен",
    OrderStatus.FILLED: "Исполнен",
    OrderStatus.CANCELLED: "Отменён",
    OrderStatus.REJECTED: "Отклонён",
}


class OrdersTab(QWidget):
    """
    Вкладка «Ордера» — отображение списка ордеров.

    Attributes:
        _event_bus: Шина событий.
        _order_provider: Провайдер ордеров (устанавливается извне).
    """

    COLUMNS = [
        "ID ордера", "Инструмент", "Направление", "Количество",
        "Цена", "Исполнено", "Статус", "Время создания",
    ]

    def __init__(self, event_bus: EventBus):
        """
        Инициализация вкладки ордеров.

        Args:
            event_bus: Шина событий.
        """
        super().__init__()
        self._event_bus = event_bus
        self._order_provider: Optional[OrderProvider] = None

        self._setup_ui()

        logger.info("OrdersTab инициализирована")

    def _setup_ui(self) -> None:
        """Построение интерфейса вкладки."""
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # ── Панель управления ──
        toolbar = QHBoxLayout()

        self._btn_refresh = QPushButton("🔄 Обновить")
        self._btn_refresh.clicked.connect(self.refresh)
        toolbar.addWidget(self._btn_refresh)

        toolbar.addStretch()

        self._info_label = QLabel("Ордера не загружены.")
        toolbar.addWidget(self._info_label)

        layout.addLayout(toolbar)

        # ── Таблица ордеров ──
        self._table = QTableWidget()
        self._table.setColumnCount(len(self.COLUMNS))
        self._table.setHorizontalHeaderLabels(self.COLUMNS)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        # Настройка ширины колонок
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)  # Инструмент
        for col in [0, 2, 3, 4, 5, 6, 7]:
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)

        layout.addWidget(self._table)

    def set_order_provider(self, provider: OrderProvider) -> None:
        """
        Установить провайдер ордеров.

        Args:
            provider: Экземпляр OrderProvider.
        """
        self._order_provider = provider
        logger.info("OrderProvider установлен для OrdersTab")

    def refresh(self) -> None:
        """Загрузить ордера через provider.get_orders() и обновить таблицу."""
        if self._order_provider is None:
            self._info_label.setText("⚠️ Провайдер ордеров не подключён")
            return

        try:
            # Публикуем событие для асинхронного получения ордеров
            self._event_bus.publish_sync(
                EventType.PROVIDER_CONNECTED,
                {"action": "get_orders"},
                source="OrdersTab",
            )
            self._info_label.setText("Запрос ордеров отправлен...")
        except Exception as exc:
            logger.warning("Не удалось запросить ордера: %s", exc)
            self._info_label.setText(f"⚠️ Ошибка: {exc}")

    def update_orders(self, orders: List[OrderInfo]) -> None:
        """
        Обновить таблицу списком ордеров (вызывается извне после получения данных).

        Args:
            orders: Список ордеров для отображения.
        """
        self._populate_table(orders)
        count = len(orders)
        if count == 0:
            self._info_label.setText("Нет активных ордеров.")
        else:
            self._info_label.setText(f"Ордеров: {count}")
        logger.debug("Таблица ордеров обновлена: %d записей", count)

    def _populate_table(self, orders: List[OrderInfo]) -> None:
        """
        Заполнить таблицу данными ордеров.

        Args:
            orders: Список ордеров.
        """
        self._table.setRowCount(0)

        for order in orders:
            row = self._table.rowCount()
            self._table.insertRow(row)

            # ID ордера
            id_item = QTableWidgetItem(order.order_id)
            id_item.setForeground(QColor("#89b4fa"))
            self._table.setItem(row, 0, id_item)

            # Инструмент
            instr_item = QTableWidgetItem(order.instrument)
            self._table.setItem(row, 1, instr_item)

            # Направление (BUY=зелёный, SELL=красный)
            side_text = order.side.value
            side_item = QTableWidgetItem(side_text)
            side_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            side_color = SIDE_COLORS.get(order.side, QColor("#cdd6f4"))
            side_item.setForeground(side_color)
            font = QFont()
            font.setBold(True)
            side_item.setFont(font)
            self._table.setItem(row, 2, side_item)

            # Количество
            qty_item = QTableWidgetItem(str(order.quantity))
            qty_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 3, qty_item)

            # Цена
            price_text = f"{order.price:.2f}" if order.price > 0 else "—"
            price_item = QTableWidgetItem(price_text)
            price_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(row, 4, price_item)

            # Исполнено
            filled_text = f"{order.filled_quantity}/{order.quantity}"
            filled_item = QTableWidgetItem(filled_text)
            filled_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 5, filled_item)

            # Статус с цветом
            status_text = STATUS_NAMES.get(order.status, order.status.value)
            status_item = QTableWidgetItem(status_text)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            status_color = STATUS_COLORS.get(order.status, QColor("#cdd6f4"))
            status_item.setForeground(status_color)
            status_item.setFont(font)
            self._table.setItem(row, 6, status_item)

            # Время создания
            time_text = order.created_at.strftime("%H:%M:%S %d.%m") if order.created_at else "—"
            time_item = QTableWidgetItem(time_text)
            time_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 7, time_item)
