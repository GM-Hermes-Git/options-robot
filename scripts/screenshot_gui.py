"""
Скрипт для создания скриншотов GUI Options Robot в headless-режиме.

Запуск:
    xvfb-run -a -s "-screen 0 1920x1080x24" python scripts/screenshot_gui.py

Создаёт скриншоты всех вкладок GUI и сохраняет в docs/screenshots/.
Каждый скриншот — полноразмерное окно 1400×900 (тёмная тема Catppuccin).

Использует виртуальный фреймбуфер Xvfb — реальный дисплей не требуется.
"""

import json
import sys
from pathlib import Path

# Добавляем корень проекта в PYTHONPATH
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPixmap

from core.event_bus import EventBus
from gui.main_window import MainWindow


# ═════════════════════════════════════════════════════════════════════
# Конфигурация
# ═════════════════════════════════════════════════════════════════════

SCREENSHOT_DIR = PROJECT_ROOT / "docs" / "screenshots"
CONFIG_PATH = PROJECT_ROOT / "config" / "settings.json"


def load_config() -> dict:
    """Загрузить конфигурацию из settings.json."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def take_screenshot(widget, filename: str) -> Path:
    """
    Сделать скриншот виджета и сохранить в PNG.

    Args:
        widget: Виджет для скриншота.
        filename: Имя файла (без пути).

    Returns:
        Path к сохранённому файлу.
    """
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    filepath = SCREENSHOT_DIR / filename

    # Захватываем содержимое виджета
    pixmap = widget.grab()
    pixmap.save(str(filepath), "PNG", 100)

    print(f"  ✓ Скриншот сохранён: {filepath}")
    return filepath


def ensure_rendered(app: QApplication):
    """
    Принудительно обработать все события Qt, чтобы интерфейс отрисовался.

    Без этого скриншоты могут быть пустыми — Qt откладывает отрисовку
    до входа в главный цикл событий. processEvents() решает эту проблему.

    Args:
        app: Экземпляр QApplication.
    """
    for _ in range(5):
        app.processEvents()


def main():
    """Главная функция — создание GUI и скриншотов всех вкладок."""
    print("=" * 60)
    print("Options Robot — Создание скриншотов GUI")
    print("=" * 60)

    # 1. Загружаем конфигурацию
    config = load_config()
    mode = config.get("trading", {}).get("mode", "moex_simulation")
    version = config.get("app", {}).get("version", "0.1")
    print(f"Конфигурация: режим={mode}, версия={version}")

    # 2. Создаём Qt Application
    app = QApplication(sys.argv)
    app.setApplicationName("Options Robot")
    app.setApplicationVersion(version)

    # 3. Создаём шину событий и главное окно
    event_bus = EventBus()
    window = MainWindow(event_bus, config)
    window.update_mode_status(mode)

    # 4. Показываем окно (нужно для grab())
    window.show()
    ensure_rendered(app)

    # 5. Скриншоты всех вкладок
    tabs = window._tabs
    screenshots = []  # [(tab_name, filename), ...]

    print(f"\nСоздание скриншотов ({tabs.count()} вкладок):")

    for i in range(tabs.count()):
        tab_name = tabs.tabText(i).replace(" ", "_")
        # Убираем emoji и спецсимволы из имени файла
        safe_name = tab_name.replace("🔌", "01").replace("📊", "02") \
                            .replace("📋", "03").replace("💼", "04") \
                            .replace("📝", "05").replace("📜", "06") \
                            .replace("🕐", "07").replace("⚙️", "08")
        filename = f"{safe_name}.png"

        # Переключаемся на вкладку
        tabs.setCurrentIndex(i)
        ensure_rendered(app)

        # Делаем скриншот
        take_screenshot(window, filename)
        screenshots.append((tabs.tabText(i), filename))

    # 6. Закрываем
    window.close()
    print(f"\n✅ Готово. Создано скриншотов: {len(screenshots)}")
    print(f"   Директория: {SCREENSHOT_DIR}")

    return screenshots


if __name__ == "__main__":
    main()
