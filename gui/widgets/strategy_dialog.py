"""
Диалог создания/редактирования стратегии (QDialog).

Предоставляет форму для ввода всех параметров стратегии:
название, базовый актив, триггер, SL/TP, список ног (legs).
Тёмная тема, модальное окно.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, QDateTime
from PyQt6.QtGui import QColor, QFont, QIntValidator, QDoubleValidator
from PyQt6.QtWidgets import (
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.strategy_manager import StrategyDefinition, Leg, StrategyStatus
from core.providers.market_data import OptionType

logger = logging.getLogger(__name__)


class StrategyDialog(QDialog):
    """
    Диалог создания/редактирования стратегии.

    Позволяет ввести все параметры стратегии, включая список ног.
    Возвращает StrategyDefinition через get_strategy_definition().

    Attributes:
        _editing_id: ID стратегии (0 для новой, >0 для редактирования).
    """

    # Заголовки колонок таблицы ног
    LEG_COLUMNS = ["№", "Тип", "Страйк", "Направление", "Кол-во", "Режим IV", "IV значение", "Множитель IV"]

    def __init__(self, parent: Optional[QWidget] = None, strategy: Optional[StrategyDefinition] = None):
        """
        Инициализация диалога.

        Args:
            parent: Родительский виджет.
            strategy: Стратегия для редактирования (None = создание новой).
        """
        super().__init__(parent)
        self._editing_id = 0
        self._strategy: Optional[StrategyDefinition] = strategy

        self.setWindowTitle("Создание стратегии" if strategy is None else "Редактирование стратегии")
        self.setMinimumWidth(700)
        self.setMinimumHeight(600)
        self.setModal(True)

        self._setup_ui()
        self._apply_theme()

        # Если редактируем — заполняем форму
        if strategy is not None:
            self.set_strategy_definition(strategy)

        logger.debug("StrategyDialog инициализирован")

    def _setup_ui(self) -> None:
        """Построение интерфейса диалога."""
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # ── Основные параметры ──
        main_group = QGroupBox("Основные параметры")
        main_layout = QFormLayout(main_group)
        main_layout.setSpacing(8)

        self._edit_name = QLineEdit()
        self._edit_name.setPlaceholderText("Например: Si Straddle")
        main_layout.addRow("Название:", self._edit_name)

        self._edit_base_asset = QLineEdit()
        self._edit_base_asset.setPlaceholderText("Si")
        main_layout.addRow("Базовый актив:", self._edit_base_asset)

        self._spin_trigger = QDoubleSpinBox()
        self._spin_trigger.setRange(0.01, 999999.0)
        self._spin_trigger.setDecimals(2)
        self._spin_trigger.setPrefix("₽ ")
        self._spin_trigger.setValue(100.0)
        main_layout.addRow("Цена триггера:", self._spin_trigger)

        self._spin_deactivation = QDoubleSpinBox()
        self._spin_deactivation.setRange(0.0, 999999.0)
        self._spin_deactivation.setDecimals(2)
        self._spin_deactivation.setPrefix("₽ ")
        self._spin_deactivation.setValue(0.0)
        self._spin_deactivation.setSpecialValueText("Не задан")
        main_layout.addRow("Порог деактивации:", self._spin_deactivation)

        self._spin_max_contracts = QSpinBox()
        self._spin_max_contracts.setRange(1, 9999)
        self._spin_max_contracts.setValue(1)
        main_layout.addRow("Макс. контрактов на ногу:", self._spin_max_contracts)

        # SL / TP
        sl_tp_layout = QHBoxLayout()
        self._spin_sl = QDoubleSpinBox()
        self._spin_sl.setRange(0.0, 999.0)
        self._spin_sl.setDecimals(1)
        self._spin_sl.setSuffix(" %")
        self._spin_sl.setValue(50.0)
        sl_tp_layout.addWidget(QLabel("SL:"))
        sl_tp_layout.addWidget(self._spin_sl)

        self._spin_tp = QDoubleSpinBox()
        self._spin_tp.setRange(0.0, 9999.0)
        self._spin_tp.setDecimals(1)
        self._spin_tp.setSuffix(" %")
        self._spin_tp.setValue(100.0)
        sl_tp_layout.addWidget(QLabel("TP:"))
        sl_tp_layout.addWidget(self._spin_tp)

        sl_tp_layout.addStretch()
        main_layout.addRow("SL / TP:", sl_tp_layout)

        # Даты (опционально)
        time_group = QGroupBox("Временные ограничения (опционально)")
        time_layout = QFormLayout(time_group)

        self._dt_start = QDateTimeEdit()
        self._dt_start.setDateTime(QDateTime.currentDateTime())
        self._dt_start.setCalendarPopup(True)
        self._dt_start.setDisplayFormat("dd.MM.yyyy HH:mm")
        self._dt_start.setSpecialValueText("Не задано")
        time_layout.addRow("Начало:", self._dt_start)

        self._dt_end = QDateTimeEdit()
        self._dt_end.setDateTime(QDateTime.currentDateTime().addDays(30))
        self._dt_end.setCalendarPopup(True)
        self._dt_end.setDisplayFormat("dd.MM.yyyy HH:mm")
        self._dt_end.setSpecialValueText("Не задано")
        time_layout.addRow("Окончание:", self._dt_end)

        layout.addWidget(main_group)
        layout.addWidget(time_group)

        # ── Таблица ног ──
        legs_group = QGroupBox("Ноги стратегии (Legs)")
        legs_layout = QVBoxLayout(legs_group)

        # Панель управления ногами
        legs_btn_row = QHBoxLayout()
        self._btn_add_leg = QPushButton("➕ Добавить ногу")
        self._btn_add_leg.clicked.connect(self._add_leg_row)
        legs_btn_row.addWidget(self._btn_add_leg)

        self._btn_remove_leg = QPushButton("➖ Удалить ногу")
        self._btn_remove_leg.clicked.connect(self._remove_leg_row)
        self._btn_remove_leg.setEnabled(False)
        legs_btn_row.addWidget(self._btn_remove_leg)

        legs_btn_row.addStretch()
        legs_layout.addLayout(legs_btn_row)

        # Таблица ног
        self._leg_table = QTableWidget()
        self._leg_table.setColumnCount(len(self.LEG_COLUMNS))
        self._leg_table.setHorizontalHeaderLabels(self.LEG_COLUMNS)
        self._leg_table.setAlternatingRowColors(True)
        self._leg_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._leg_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._leg_table.itemSelectionChanged.connect(self._on_leg_selection_changed)

        # Настройка ширины колонок
        header = self._leg_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for col in [0, 1, 3, 5]:
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)

        legs_layout.addWidget(self._leg_table)
        layout.addWidget(legs_group)

        # ── Кнопки диалога ──
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _apply_theme(self) -> None:
        """Применить тёмную тему Catppuccin Mocha к диалогу."""
        self.setStyleSheet("""
            QDialog {
                background-color: #1e1e2e;
                color: #cdd6f4;
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
            QLineEdit, QDoubleSpinBox, QSpinBox, QDateTimeEdit, QComboBox {
                background-color: #313244;
                color: #cdd6f4;
                border: 1px solid #45475a;
                border-radius: 4px;
                padding: 4px 8px;
                min-height: 24px;
            }
            QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus {
                border-color: #89b4fa;
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
            QTableWidget {
                background-color: #181825;
                color: #cdd6f4;
                gridline-color: #313244;
                border: 1px solid #313244;
                border-radius: 4px;
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
            QComboBox::drop-down {
                border: none;
            }
            QComboBox QAbstractItemView {
                background-color: #313244;
                color: #cdd6f4;
                selection-background-color: #45475a;
            }
            QSpinBox::up-button, QDoubleSpinBox::up-button {
                background-color: #45475a;
                border: none;
            }
            QSpinBox::down-button, QDoubleSpinBox::down-button {
                background-color: #45475a;
                border: none;
            }
        """)

    # ──────────────────────────────────────────────────────────
    # Управление ногами
    # ──────────────────────────────────────────────────────────

    def _add_leg_row(self) -> None:
        """Добавить строку для новой ноги в таблицу."""
        row = self._leg_table.rowCount()
        self._leg_table.insertRow(row)

        # №
        num_item = QTableWidgetItem(str(row + 1))
        num_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        num_item.setFlags(num_item.flags() & ~Qt.ItemFlag.ItemIsEditable)  # readonly
        self._leg_table.setItem(row, 0, num_item)

        # Тип (CALL/PUT)
        type_combo = QComboBox()
        type_combo.addItem("CALL")
        type_combo.addItem("PUT")
        type_combo.setCurrentIndex(0)
        self._leg_table.setCellWidget(row, 1, type_combo)

        # Страйк
        strike_spin = QDoubleSpinBox()
        strike_spin.setRange(0.01, 999999.0)
        strike_spin.setDecimals(2)
        strike_spin.setValue(100.0)
        strike_spin.setPrefix("₽ ")
        self._leg_table.setCellWidget(row, 2, strike_spin)

        # Направление (Buy/Sell)
        direction_combo = QComboBox()
        direction_combo.addItem("Buy", 1)
        direction_combo.addItem("Sell", -1)
        direction_combo.setCurrentIndex(0)
        self._leg_table.setCellWidget(row, 3, direction_combo)

        # Кол-во
        qty_spin = QSpinBox()
        qty_spin.setRange(1, 9999)
        qty_spin.setValue(1)
        self._leg_table.setCellWidget(row, 4, qty_spin)

        # Режим IV
        iv_mode_combo = QComboBox()
        iv_mode_combo.addItem("market")
        iv_mode_combo.addItem("manual")
        iv_mode_combo.setCurrentIndex(0)
        self._leg_table.setCellWidget(row, 5, iv_mode_combo)

        # IV значение
        iv_spin = QDoubleSpinBox()
        iv_spin.setRange(0.0, 999.0)
        iv_spin.setDecimals(2)
        iv_spin.setSuffix(" %")
        iv_spin.setValue(0.0)
        iv_spin.setEnabled(False)  # только для manual режима
        self._leg_table.setCellWidget(row, 6, iv_spin)

        # Множитель IV
        iv_mult_spin = QDoubleSpinBox()
        iv_mult_spin.setRange(0.01, 10.0)
        iv_mult_spin.setDecimals(2)
        iv_mult_spin.setSingleStep(0.1)
        iv_mult_spin.setValue(1.0)
        self._leg_table.setCellWidget(row, 7, iv_mult_spin)

        # Связываем переключение режима IV с доступностью IV значения
        iv_mode_combo.currentTextChanged.connect(
            lambda text, s=iv_spin: s.setEnabled(text == "manual")
        )

        logger.debug("Добавлена нога #%d", row + 1)

    def _remove_leg_row(self) -> None:
        """Удалить выбранную строку из таблицы ног."""
        selected = self._leg_table.selectedItems()
        if not selected:
            return
        row = selected[0].row()
        self._leg_table.removeRow(row)
        self._renumber_legs()
        logger.debug("Удалена нога #%d", row + 1)

    def _renumber_legs(self) -> None:
        """Перенумеровать строки в таблице ног после удаления."""
        for row in range(self._leg_table.rowCount()):
            item = self._leg_table.item(row, 0)
            if item:
                item.setText(str(row + 1))

    def _on_leg_selection_changed(self) -> None:
        """Обновить состояние кнопки удаления ноги."""
        has_selection = len(self._leg_table.selectedItems()) > 0
        self._btn_remove_leg.setEnabled(has_selection)

    # ──────────────────────────────────────────────────────────
    # Получение / установка данных
    # ──────────────────────────────────────────────────────────

    def get_strategy_definition(self) -> StrategyDefinition:
        """
        Собрать данные из формы и вернуть StrategyDefinition.

        Returns:
            Стратегия с параметрами из формы.

        Raises:
            ValueError: Если данные некорректны.
        """
        if not self._validate():
            raise ValueError("Некорректные данные формы")

        name = self._edit_name.text().strip()
        base_asset = self._edit_base_asset.text().strip().upper()
        trigger = self._spin_trigger.value()
        deactivation = self._spin_deactivation.value()
        max_contracts = self._spin_max_contracts.value()
        sl = self._spin_sl.value()
        tp = self._spin_tp.value()

        # Даты
        start_time = None
        end_time = None
        if not self._dt_start.dateTime().isNull():
            start_time = self._dt_start.dateTime().toPyDateTime()
        if not self._dt_end.dateTime().isNull():
            end_time = self._dt_end.dateTime().toPyDateTime()

        # Ноги
        legs = self._collect_legs()

        return StrategyDefinition(
            strategy_id=self._editing_id,
            name=name,
            base_asset=base_asset,
            status=StrategyStatus.CONFIGURED,
            legs=legs,
            trigger_level=trigger,
            trigger_deactivation_threshold=deactivation,
            start_time=start_time,
            end_time=end_time,
            max_contracts_per_leg=max_contracts,
            sl_percent=sl,
            tp_percent=tp,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

    def set_strategy_definition(self, definition: StrategyDefinition) -> None:
        """
        Заполнить форму данными из существующей стратегии (для редактирования).

        Args:
            definition: Стратегия для редактирования.
        """
        self._editing_id = definition.strategy_id
        self._edit_name.setText(definition.name)
        self._edit_base_asset.setText(definition.base_asset)
        self._spin_trigger.setValue(definition.trigger_level)
        self._spin_deactivation.setValue(definition.trigger_deactivation_threshold)
        self._spin_max_contracts.setValue(definition.max_contracts_per_leg)
        self._spin_sl.setValue(definition.sl_percent)
        self._spin_tp.setValue(definition.tp_percent)

        if definition.start_time:
            self._dt_start.setDateTime(QDateTime(
                definition.start_time.year,
                definition.start_time.month,
                definition.start_time.day,
                definition.start_time.hour,
                definition.start_time.minute,
            ))
        if definition.end_time:
            self._dt_end.setDateTime(QDateTime(
                definition.end_time.year,
                definition.end_time.month,
                definition.end_time.day,
                definition.end_time.hour,
                definition.end_time.minute,
            ))

        # Очищаем и заполняем ноги
        self._leg_table.setRowCount(0)
        for leg in definition.legs:
            self._add_leg_row()
            row = self._leg_table.rowCount() - 1

            # Тип
            type_combo: QComboBox = self._leg_table.cellWidget(row, 1)
            type_combo.setCurrentText(leg.option_type.value)

            # Страйк
            strike_spin: QDoubleSpinBox = self._leg_table.cellWidget(row, 2)
            strike_spin.setValue(leg.strike)

            # Направление
            dir_combo: QComboBox = self._leg_table.cellWidget(row, 3)
            dir_combo.setCurrentIndex(0 if leg.sign == 1 else 1)

            # Кол-во
            qty_spin: QSpinBox = self._leg_table.cellWidget(row, 4)
            qty_spin.setValue(leg.quantity)

            # Режим IV
            iv_mode_combo: QComboBox = self._leg_table.cellWidget(row, 5)
            iv_mode_combo.setCurrentText(leg.iv_mode)

            # IV значение
            iv_spin: QDoubleSpinBox = self._leg_table.cellWidget(row, 6)
            if leg.manual_iv is not None:
                iv_spin.setValue(leg.manual_iv)
            iv_spin.setEnabled(leg.iv_mode == "manual")

            # Множитель IV
            iv_mult_spin: QDoubleSpinBox = self._leg_table.cellWidget(row, 7)
            iv_mult_spin.setValue(leg.iv_multiplier)

    def _collect_legs(self) -> List[Leg]:
        """
        Собрать список ног из таблицы.

        Returns:
            Список Leg.

        Raises:
            ValueError: Если данные ног некорректны.
        """
        legs: List[Leg] = []

        for row in range(self._leg_table.rowCount()):
            type_combo: QComboBox = self._leg_table.cellWidget(row, 1)
            strike_spin: QDoubleSpinBox = self._leg_table.cellWidget(row, 2)
            dir_combo: QComboBox = self._leg_table.cellWidget(row, 3)
            qty_spin: QSpinBox = self._leg_table.cellWidget(row, 4)
            iv_mode_combo: QComboBox = self._leg_table.cellWidget(row, 5)
            iv_spin: QDoubleSpinBox = self._leg_table.cellWidget(row, 6)
            iv_mult_spin: QDoubleSpinBox = self._leg_table.cellWidget(row, 7)

            option_type = OptionType(type_combo.currentText())
            strike = strike_spin.value()
            sign = dir_combo.currentData()
            quantity = qty_spin.value()
            iv_mode = iv_mode_combo.currentText()
            manual_iv = iv_spin.value() if iv_mode == "manual" else None
            iv_mult = iv_mult_spin.value()

            leg = Leg(
                leg_index=row,
                option_type=option_type,
                strike=strike,
                sign=sign,
                quantity=quantity,
                iv_mode=iv_mode,
                manual_iv=manual_iv,
                iv_multiplier=iv_mult,
            )
            legs.append(leg)

        return legs

    def _validate(self) -> bool:
        """
        Проверить корректность введённых данных.

        Returns:
            True, если данные корректны.
        """
        name = self._edit_name.text().strip()
        if not name:
            QMessageBox.warning(self, "Ошибка", "Название стратегии не может быть пустым.")
            self._edit_name.setFocus()
            return False

        base_asset = self._edit_base_asset.text().strip()
        if not base_asset:
            QMessageBox.warning(self, "Ошибка", "Базовый актив не может быть пустым.")
            self._edit_base_asset.setFocus()
            return False

        if self._leg_table.rowCount() == 0:
            QMessageBox.warning(self, "Ошибка", "Добавьте хотя бы одну ногу.")
            return False

        # Проверяем, что в ногах корректные страйки
        for row in range(self._leg_table.rowCount()):
            strike_spin: QDoubleSpinBox = self._leg_table.cellWidget(row, 2)
            if strike_spin.value() <= 0:
                QMessageBox.warning(
                    self, "Ошибка",
                    f"Страйк ноги #{row + 1} должен быть положительным."
                )
                return False

        return True

    def _on_accept(self) -> None:
        """Обработчик нажатия OK — проверяет валидность и закрывает диалог."""
        if self._validate():
            self.accept()
