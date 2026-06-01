"""
Вкладка «Стратегии» — таблица стратегий и управление ими.

Заменяет PlaceholderTab("Стратегии", ...) в MainWindow.
Отображает список стратегий из StrategyManager с цветовой индикацией статусов,
позволяет создавать, запускать, останавливать и удалять стратегии.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, QTimer
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
    QMessageBox,
)

from core.event_bus import EventBus, EventType, Event
from core.strategy_manager import StrategyManager, StrategyDefinition, StrategyStatus
from gui.widgets.strategy_dialog import StrategyDialog

logger = logging.getLogger(__name__)

# Цвета статусов (Catppuccin Mocha)
STATUS_COLORS = {
    StrategyStatus.CONFIGURED: QColor("#6c7086"),  # серый
    StrategyStatus.WAITING: QColor("#89b4fa"),     # голубой
    StrategyStatus.ACTIVE: QColor("#89b4fa"),      # синий
    StrategyStatus.TRIGGERED: QColor("#f9e2af"),   # жёлтый
    StrategyStatus.BUILDING: QColor("#fab387"),     # оранжевый
    StrategyStatus.POSITION_OPEN: QColor("#a6e3a1"),# зелёный
    StrategyStatus.CLOSING: QColor("#f38ba8"),      # розовый
    StrategyStatus.STOPPED: QColor("#f38ba8"),      # красный
}

# Текстовые названия статусов
STATUS_NAMES = {
    StrategyStatus.CONFIGURED: "Сконфигурирована",
    StrategyStatus.WAITING: "Ожидание",
    StrategyStatus.ACTIVE: "Активна",
    StrategyStatus.TRIGGERED: "Триггер сработал",
    StrategyStatus.BUILDING: "Набор позиции",
    StrategyStatus.POSITION_OPEN: "Позиция открыта",
    StrategyStatus.CLOSING: "Закрытие",
    StrategyStatus.STOPPED: "Остановлена",
}


class StrategyTab(QWidget):
    """
    Вкладка «Стратегии» — отображение и управление стратегиями.

    Attributes:
        _event_bus: Шина событий для подписки на события.
        _strategy_manager: Менеджер стратегий (устанавливается извне).
    """

    COLUMNS = [
        "ID", "Название", "Базовый актив", "Статус",
        "Триггер", "SL / TP", "Ног", "Создана",
    ]

    def __init__(self, event_bus: EventBus):
        """
        Инициализация вкладки стратегий.

        Args:
            event_bus: Шина событий.
        """
        super().__init__()
        self._event_bus = event_bus
        self._strategy_manager: Optional[StrategyManager] = None

        # ID подписок на события (для отписки)
        self._sub_ids: List[int] = []

        self._setup_ui()
        self._subscribe_events()

        logger.info("StrategyTab инициализирована")

    def _setup_ui(self) -> None:
        """Построение интерфейса вкладки."""
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # ── Верхняя панель инструментов ──
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self._btn_create = QPushButton("➕ Создать")
        self._btn_create.clicked.connect(self._on_create)
        toolbar.addWidget(self._btn_create)

        self._btn_start = QPushButton("▶ Запустить")
        self._btn_start.clicked.connect(self._on_start)
        self._btn_start.setEnabled(False)
        toolbar.addWidget(self._btn_start)

        self._btn_stop = QPushButton("⏹ Остановить")
        self._btn_stop.clicked.connect(self._on_stop)
        self._btn_stop.setEnabled(False)
        toolbar.addWidget(self._btn_stop)

        self._btn_delete = QPushButton("🗑 Удалить")
        self._btn_delete.clicked.connect(self._on_delete)
        self._btn_delete.setEnabled(False)
        toolbar.addWidget(self._btn_delete)

        toolbar.addStretch()

        self._btn_refresh = QPushButton("🔄 Обновить")
        self._btn_refresh.clicked.connect(self.refresh)
        toolbar.addWidget(self._btn_refresh)

        layout.addLayout(toolbar)

        # ── Таблица стратегий ──
        self._table = QTableWidget()
        self._table.setColumnCount(len(self.COLUMNS))
        self._table.setHorizontalHeaderLabels(self.COLUMNS)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)

        # Настройка ширины колонок
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)  # Название
        for col in [0, 2, 3, 4, 5, 6, 7]:
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)

        layout.addWidget(self._table)

        # ── Информационная строка ──
        self._info_label = QLabel("Стратегии не загружены. Нажмите «➕ Создать» для добавления.")
        layout.addWidget(self._info_label)

    def _subscribe_events(self) -> None:
        """Подписаться на события EventBus для обновления списка."""
        try:
            # Используем синхронную обёртку для обработки событий в GUI-потоке
            sub_id = self._event_bus.subscribe(
                EventType.STRATEGY_STOPPED,
                self._on_strategy_stopped_event,
                priority=90,
            )
            self._sub_ids.append(sub_id)
            logger.debug("StrategyTab подписан на STRATEGY_STOPPED")
        except Exception as exc:
            logger.warning("Не удалось подписаться на события: %s", exc)

    async def _on_strategy_stopped_event(self, event: Event) -> None:
        """Обработчик события STRATEGY_STOPPED — обновить таблицу."""
        self.refresh()

    def set_strategy_manager(self, manager: StrategyManager) -> None:
        """
        Установить менеджер стратегий.

        Args:
            manager: Экземпляр StrategyManager.
        """
        self._strategy_manager = manager
        self.refresh()
        logger.info("StrategyManager установлен")

    def refresh(self) -> None:
        """Перезагрузить список стратегий из StrategyManager и обновить таблицу."""
        if self._strategy_manager is None:
            self._info_label.setText("⚠️ Менеджер стратегий не подключён")
            return

        strategies = self._strategy_manager.get_all_strategies()
        self._populate_table(strategies)

        count = len(strategies)
        if count == 0:
            self._info_label.setText("Нет стратегий. Нажмите «➕ Создать» для добавления.")
        else:
            self._info_label.setText(f"Загружено стратегий: {count}")

        logger.debug("Таблица стратегий обновлена: %d записей", count)

    def _populate_table(self, strategies: List[StrategyDefinition]) -> None:
        """
        Заполнить таблицу данными стратегий.

        Args:
            strategies: Список стратегий для отображения.
        """
        self._table.setRowCount(0)

        for strategy in strategies:
            row = self._table.rowCount()
            self._table.insertRow(row)

            # ID
            id_item = QTableWidgetItem(str(strategy.strategy_id))
            id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 0, id_item)

            # Название
            name_item = QTableWidgetItem(strategy.name)
            name_item.setToolTip(strategy.name)
            self._table.setItem(row, 1, name_item)

            # Базовый актив
            asset_item = QTableWidgetItem(strategy.base_asset)
            asset_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            asset_item.setForeground(QColor("#89b4fa"))
            self._table.setItem(row, 2, asset_item)

            # Статус с цветовой индикацией
            status_text = STATUS_NAMES.get(strategy.status, strategy.status.value)
            status_item = QTableWidgetItem(status_text)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            status_color = STATUS_COLORS.get(strategy.status, QColor("#cdd6f4"))
            status_item.setForeground(status_color)
            # Жирный шрифт для статуса
            font = QFont()
            font.setBold(True)
            status_item.setFont(font)
            self._table.setItem(row, 3, status_item)

            # Триггер
            trigger_text = f"{strategy.trigger_level:.2f}" if strategy.trigger_level > 0 else "—"
            trigger_item = QTableWidgetItem(trigger_text)
            trigger_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(row, 4, trigger_item)

            # SL / TP
            sl_tp_text = f"{strategy.sl_percent:.0f}% / {strategy.tp_percent:.0f}%"
            sl_tp_item = QTableWidgetItem(sl_tp_text)
            sl_tp_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 5, sl_tp_item)

            # Ног (количество legs)
            legs_count = len(strategy.legs)
            legs_item = QTableWidgetItem(str(legs_count))
            legs_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 6, legs_item)

            # Создана
            created_str = strategy.created_at.strftime("%d.%m.%Y %H:%M") if strategy.created_at else "—"
            created_item = QTableWidgetItem(created_str)
            created_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 7, created_item)

    # ──────────────────────────────────────────────────────────
    # Обработчики кнопок
    # ──────────────────────────────────────────────────────────

    def _on_create(self) -> None:
        """Открыть диалог создания новой стратегии."""
        dialog = StrategyDialog(self)
        if dialog.exec() == StrategyDialog.DialogCode.Accepted:
            try:
                definition = dialog.get_strategy_definition()
            except ValueError as exc:
                QMessageBox.warning(self, "Ошибка", f"Некорректные данные: {exc}")
                return

            if self._strategy_manager is None:
                QMessageBox.warning(self, "Ошибка", "Менеджер стратегий не подключён")
                return

            # Публикуем событие для асинхронного создания стратегии
            self._event_bus.publish_sync(
                EventType.PROVIDER_CONNECTED,  # используем как триггер создания
                {
                    "action": "create_strategy",
                    "definition": definition.to_dict(),
                },
                source="StrategyTab",
            )
            # Обновляем таблицу (менеджер асинхронно добавит стратегию)
            QMessageBox.information(
                self, "Создание стратегии",
                f"Стратегия «{definition.name}» отправлена на создание.\n"
                "Обновите таблицу для отображения.",
            )

    def _on_start(self) -> None:
        """Запустить выбранную стратегию."""
        strategy_id = self._get_selected_id()
        if strategy_id is None:
            return

        if self._strategy_manager is None:
            QMessageBox.warning(self, "Ошибка", "Менеджер стратегий не подключён")
            return

        self._event_bus.publish_sync(
            EventType.PROVIDER_CONNECTED,
            {
                "action": "start_strategy",
                "strategy_id": strategy_id,
            },
            source="StrategyTab",
        )
        self.refresh()
        logger.info("Запрос на запуск стратегии #%d отправлен", strategy_id)

    def _on_stop(self) -> None:
        """Остановить выбранную стратегию."""
        strategy_id = self._get_selected_id()
        if strategy_id is None:
            return

        if self._strategy_manager is None:
            QMessageBox.warning(self, "Ошибка", "Менеджер стратегий не подключён")
            return

        strategy = self._strategy_manager.get_strategy(strategy_id)
        if strategy and strategy.status == StrategyStatus.STOPPED:
            QMessageBox.information(self, "Информация", "Стратегия уже остановлена.")
            return

        reply = QMessageBox.question(
            self, "Подтверждение",
            f"Остановить стратегию #{strategy_id}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._event_bus.publish_sync(
            EventType.PROVIDER_CONNECTED,
            {
                "action": "stop_strategy",
                "strategy_id": strategy_id,
            },
            source="StrategyTab",
        )
        self.refresh()
        logger.info("Запрос на остановку стратегии #%d отправлен", strategy_id)

    def _on_delete(self) -> None:
        """Удалить выбранную стратегию."""
        strategy_id = self._get_selected_id()
        if strategy_id is None:
            return

        if self._strategy_manager is None:
            QMessageBox.warning(self, "Ошибка", "Менеджер стратегий не подключён")
            return

        reply = QMessageBox.question(
            self, "Подтверждение",
            f"Удалить стратегию #{strategy_id}?\nЭто действие нельзя отменить.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._event_bus.publish_sync(
            EventType.PROVIDER_CONNECTED,
            {
                "action": "delete_strategy",
                "strategy_id": strategy_id,
            },
            source="StrategyTab",
        )
        self.refresh()
        logger.info("Запрос на удаление стратегии #%d отправлен", strategy_id)

    # ──────────────────────────────────────────────────────────
    # Вспомогательные методы
    # ──────────────────────────────────────────────────────────

    def _get_selected_id(self) -> Optional[int]:
        """
        Получить ID выбранной стратегии из таблицы.

        Returns:
            ID стратегии или None, если ничего не выбрано.
        """
        selected = self._table.selectedItems()
        if not selected:
            return None
        row = selected[0].row()
        id_item = self._table.item(row, 0)
        if id_item is None:
            return None
        try:
            return int(id_item.text())
        except ValueError:
            return None

    def _on_selection_changed(self) -> None:
        """Обновить состояние кнопок при изменении выделения."""
        has_selection = len(self._table.selectedItems()) > 0
        self._btn_start.setEnabled(has_selection)
        self._btn_stop.setEnabled(has_selection)
        self._btn_delete.setEnabled(has_selection)

    def _get_status_color(self, status: StrategyStatus) -> QColor:
        """
        Получить цвет для указанного статуса стратегии.

        Args:
            status: Статус стратегии.

        Returns:
            Цвет в схеме Catppuccin Mocha.
        """
        return STATUS_COLORS.get(status, QColor("#cdd6f4"))
