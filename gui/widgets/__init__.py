"""
Виджеты GUI для вкладок приложения Options Robot.

Доступные виджеты:
    - StrategyTab: Вкладка управления стратегиями
    - StrategyDialog: Диалог создания/редактирования стратегии
    - OrdersTab: Вкладка списка ордеров
    - LogTab: Вкладка лога событий
"""

from gui.widgets.strategy_tab import StrategyTab
from gui.widgets.strategy_dialog import StrategyDialog
from gui.widgets.orders_tab import OrdersTab
from gui.widgets.log_tab import LogTab

__all__ = [
    "StrategyTab",
    "StrategyDialog",
    "OrdersTab",
    "LogTab",
]
