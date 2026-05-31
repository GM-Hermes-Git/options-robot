"""
Точка входа приложения Options Robot.

Инициализирует все модули, загружает конфигурацию, настраивает
логирование и запускает графический интерфейс.

Архитектура запуска:
    1. Загрузить settings.json
    2. Настроить логирование
    3. Создать EventBus
    4. Инициализировать провайдеры данных и ордеров
    5. Инициализировать GreeksEngine
    6. Создать GUI (MainWindow)
    7. Подключить провайдеры к GUI
    8. Настроить мост asyncio ↔ PyQt (через qasync)
    9. Запустить цикл обработки событий

Использование:
    python main.py                    # Запуск с настройками по умолчанию
    python main.py --config config/custom.json  # С пользовательской конфигурацией
"""

import argparse
import asyncio
import json
import logging
import signal
import sys
from pathlib import Path
from typing import Dict, Any

# PyQt6 импортируется после настройки asyncio
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer
import qasync

from core.event_bus import EventBus, EventType
from core.providers.moex_provider import MoexDataProvider
from core.providers.alor_provider import AlorDataProvider, AlorOrderProvider
from core.providers.simulated_orders import SimulatedOrderProvider
from core.greeks_engine import GreeksEngine
from core.strategy_manager import StrategyManager
from core.trigger_engine import TriggerEngine
from core.order_manager import OrderManager
from core.delta_hedger import DeltaHedger
from core.risk_manager import RiskManager
from gui.main_window import MainWindow
from utils.logger import setup_logging, get_logger

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """
    Разобрать аргументы командной строки.

    Returns:
        Объект с аргументами.
    """
    parser = argparse.ArgumentParser(
        description="Options Robot — торговый робот для опционов MOEX",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python main.py                         # Запуск с config/settings.json
  python main.py --config custom.json    # Своя конфигурация
  python main.py --mode demo             # Принудительный демо-режим
  python main.py --no-gui                # Консольный режим (без GUI)
        """,
    )
    parser.add_argument(
        "--config", "-c",
        default="config/settings.json",
        help="Путь к файлу конфигурации (по умолчанию: config/settings.json)",
    )
    parser.add_argument(
        "--mode", "-m",
        choices=["moex_simulation", "alor_demo", "alor_production"],
        help="Режим работы (переопределяет настройку из конфигурации)",
    )
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help="Запуск без графического интерфейса (консольный режим)",
    )
    parser.add_argument(
        "--version", "-v",
        action="version",
        version="Options Robot v0.1.0",
    )
    return parser.parse_args()


def load_config(config_path: str) -> Dict[str, Any]:
    """
    Загрузить конфигурацию из JSON-файла.

    Если существует settings_local.json в той же директории —
    его настройки переопределяют основные (для локальных секретов).

    Args:
        config_path: Путь к файлу конфигурации.

    Returns:
        Словарь с настройками.

    Raises:
        FileNotFoundError: Если файл конфигурации не найден.
        json.JSONDecodeError: Если JSON повреждён.
    """
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Файл конфигурации не найден: {config_file.absolute()}")

    with open(config_file, "r", encoding="utf-8") as f:
        config = json.load(f)

    # Локальные настройки (токены и секреты) — переопределяют основные
    local_file = config_file.parent / "settings_local.json"
    if local_file.exists():
        with open(local_file, "r", encoding="utf-8") as f:
            local_config = json.load(f)
        # Глубокое слияние (поверхностное для простоты)
        config.update(local_config)
        logger.info("Загружена локальная конфигурация: %s", local_file)

    logger.info(
        "Конфигурация загружена: %s (режим: %s)",
        config_file, config.get("trading", {}).get("mode", "не указан"),
    )
    return config


class Application:
    """
    Главный класс приложения Options Robot.

    Управляет жизненным циклом:
        - Инициализация модулей
        - Запуск / остановка
        - Связывание GUI с бизнес-логикой через EventBus
    """

    def __init__(self, config_path: str, cli_mode: str = None, no_gui: bool = False):
        """
        Инициализация приложения.

        Args:
            config_path: Путь к файлу конфигурации.
            cli_mode: Режим работы из командной строки (переопределяет конфиг).
            no_gui: True для консольного режима.
        """
        # 1. Загружаем конфигурацию
        self.config = load_config(config_path)

        # 2. Настраиваем логирование
        setup_logging(self.config)
        self.logger = get_logger(__name__)
        self.logger.info("=" * 60)
        self.logger.info("Options Robot v%s запускается",
                        self.config.get("app", {}).get("version", "0.1"))
        self.logger.info("=" * 60)

        # 3. Определяем режим работы
        self.mode = cli_mode or self.config.get("trading", {}).get("mode", "moex_simulation")
        self.no_gui = no_gui
        self.logger.info("Режим работы: %s", self.mode)

        # 4. Шина событий
        self.event_bus = EventBus()

        # 5. Движок греков (единый для всех стратегий)
        risk_free_rate = self.config.get("trading", {}).get("risk_free_rate", 0.085)
        self.greeks_engine = GreeksEngine(risk_free_rate=risk_free_rate)

        # 6. Провайдеры данных (инициализируются в setup_providers)
        self.data_provider = None
        self.order_provider = None

        # 7. Менеджеры (заглушки на Этапе 1)
        self.strategy_manager = StrategyManager(self.event_bus, self.config)
        self.trigger_engine = TriggerEngine(self.event_bus, self.config)
        self.order_manager = OrderManager(self.event_bus, self.config)
        self.delta_hedger = DeltaHedger(self.event_bus, self.config)
        self.risk_manager = RiskManager(self.event_bus, self.config)

        # 8. GUI (None в консольном режиме)
        self.qt_app = None
        self.main_window = None

        self.logger.info("Приложение инициализировано")

    def setup_providers(self) -> None:
        """
        Создать и настроить провайдеры данных и ордеров согласно режиму работы.

        Режимы:
            moex_simulation  — MOEX ISS API + SimulatedOrderProvider
            alor_demo        — Alor Demo API + AlorOrderProvider
            alor_production  — Alor Production API + AlorOrderProvider
        """
        self.logger.info("Настройка провайдеров для режима: %s", self.mode)

        if self.mode == "moex_simulation":
            self.data_provider = MoexDataProvider(self.event_bus, self.config)
            self.order_provider = SimulatedOrderProvider(self.event_bus, self.config)
        elif self.mode in ("alor_demo", "alor_production"):
            # Заглушки — полная реализация на Этапе 4
            self.data_provider = AlorDataProvider(self.event_bus, self.config)
            self.order_provider = AlorOrderProvider(self.event_bus, self.config)
        else:
            raise ValueError(f"Неизвестный режим работы: {self.mode}")

        self.logger.info(
            "Провайдеры: данные=%s, ордера=%s",
            type(self.data_provider).__name__,
            type(self.order_provider).__name__,
        )

    async def start(self) -> None:
        """
        Запустить приложение.

        1. Подключить провайдеры.
        2. Запустить GUI (или консольный режим).
        3. Войти в главный цикл обработки событий.
        """
        self.logger.info("Запуск приложения...")

        # Подключаем провайдеры
        if self.data_provider:
            connected = await self.data_provider.connect()
            if connected and self.main_window:
                self.main_window.update_provider_status(
                    type(self.data_provider).__name__, True
                )
                self.main_window._tab_connection.update_connection_status(True)

        if self.order_provider:
            await self.order_provider.connect()

        if self.no_gui:
            # Консольный режим: печатаем статус и ждём Ctrl+C
            self.logger.info("Консольный режим. Нажмите Ctrl+C для выхода.")
            print(f"\n{'='*50}")
            print(f"Options Robot v{self.config.get('app', {}).get('version', '0.1')}")
            print(f"Режим: {self.mode}")
            print(f"Провайдер данных: {type(self.data_provider).__name__}")
            print(f"Провайдер ордеров: {type(self.order_provider).__name__}")
            print(f"Event Bus: {self.event_bus.total_subscribers()} подписчиков")
            print(f"{'='*50}\n")
            print("Для тестирования можно импортировать модули вручную.")
            print("Нажмите Ctrl+C для выхода.\n")

            # Бесконечное ожидание
            try:
                while True:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                pass
        else:
            # GUI режим
            self.logger.info("Запуск графического интерфейса...")
            self.main_window.show()

    async def stop(self) -> None:
        """Корректная остановка приложения."""
        self.logger.info("Остановка приложения...")

        # Публикуем событие остановки
        await self.event_bus.publish(
            EventType.APP_SHUTDOWN,
            {"reason": "user_request"},
            source="Application",
        )

        # Отключаем провайдеры
        if self.data_provider:
            await self.data_provider.disconnect()
        if self.order_provider:
            await self.order_provider.disconnect()

        self.logger.info("Приложение остановлено")


def setup_gui(app_instance: Application) -> None:
    """
    Настроить графический интерфейс.

    Args:
        app_instance: Экземпляр Application.
    """
    config = app_instance.config

    # Создаём Qt application
    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("Options Robot")
    qt_app.setOrganizationName("OptionsRobot")
    qt_app.setApplicationVersion(config.get("app", {}).get("version", "0.1"))

    # Настройка шрифтов
    from PyQt6.QtGui import QFont
    font_family = config.get("gui", {}).get("font_family", "Segoe UI")
    font_size = config.get("gui", {}).get("font_size", 10)
    qt_app.setFont(QFont(font_family, font_size))

    app_instance.qt_app = qt_app

    # Создаём главное окно
    main_window = MainWindow(app_instance.event_bus, config)
    app_instance.main_window = main_window

    # Передаём провайдеры в GUI
    if app_instance.data_provider:
        main_window.set_data_provider(app_instance.data_provider)
    if app_instance.order_provider:
        main_window.set_order_provider(app_instance.order_provider)

    # Обновляем статус в GUI
    main_window.update_mode_status(app_instance.mode)


async def run_gui(app_instance: Application) -> None:
    """
    Запустить асинхронный цикл PyQt + asyncio через qasync.

    Args:
        app_instance: Экземпляр Application.
    """
    # Создаём мост между asyncio и Qt event loop
    loop = qasync.QEventLoop(app_instance.qt_app)
    asyncio.set_event_loop(loop)

    # Регистрируем обработчики сигналов для корректного завершения
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.ensure_future(
                app_instance.stop()
            ))
        except NotImplementedError:
            # Windows не поддерживает add_signal_handler
            pass

    # Запускаем приложение
    await app_instance.start()

    # Входим в главный цикл Qt
    with loop:
        loop.run_forever()


def main() -> None:
    """
    Главная функция — точка входа.

    Порядок запуска:
        1. Разобрать аргументы командной строки
        2. Создать экземпляр Application
        3. Настроить провайдеры
        4. Если GUI — настроить интерфейс и запустить цикл обработки
        5. Если консоль — запустить asyncio.run()
    """
    args = parse_args()

    try:
        # Создаём приложение
        app = Application(
            config_path=args.config,
            cli_mode=args.mode,
            no_gui=args.no_gui,
        )

        # Настраиваем провайдеры
        app.setup_providers()

        if args.no_gui:
            # Консольный режим
            try:
                asyncio.run(app.start())
            except KeyboardInterrupt:
                logger.info("Получен сигнал прерывания (Ctrl+C)")
                asyncio.run(app.stop())
        else:
            # GUI режим
            setup_gui(app)

            # Запускаем асинхронный цикл
            try:
                asyncio.run(run_gui(app))
            except KeyboardInterrupt:
                logger.info("Получен сигнал прерывания")
                asyncio.run(app.stop())

    except FileNotFoundError as exc:
        print(f"❌ Ошибка: {exc}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print(f"❌ Ошибка чтения конфигурации: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        logger.error("Критическая ошибка: %s", exc, exc_info=True)
        print(f"❌ Критическая ошибка: {exc}", file=sys.stderr)
        sys.exit(1)

    logger.info("Выход из приложения")


if __name__ == "__main__":
    main()
