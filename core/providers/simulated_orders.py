"""
Симулятор ордеров для демо-режима и отладки.

Реализует OrderProvider, имитируя поведение торговой системы:
    - Ордера принимаются и «исполняются» по рыночным ценам.
    - Может имитировать частичное исполнение.
    - Ведёт учёт виртуальных позиций.
    - Все операции логируются.

Используется:
    - На этапах 1-3 разработки (без реального подключения к брокеру).
    - Для тестирования стратегий без риска реальных сделок.
    - Для отладки взаимодействия модулей через EventBus.

Использование:
    from core.providers.simulated_orders import SimulatedOrderProvider

    order_provider = SimulatedOrderProvider(event_bus, config)
    await order_provider.connect()

    request = OrderRequest(
        instrument="Si-6.25M270625CA85000",
        side=OrderSide.BUY,
        quantity=1,
        price=150.0,
    )
    order = await order_provider.place_order(request)
"""

import asyncio
import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.event_bus import EventBus, EventType
from core.providers.market_data import (
    OrderProvider,
    OrderRequest,
    OrderInfo,
    OrderSide,
    OrderStatus,
    Position,
)

logger = logging.getLogger(__name__)


class SimulatedOrderProvider(OrderProvider):
    """
    Симулятор торговых операций.

    Имитирует полный цикл жизни ордера:
    1. Получение заявки (place_order)
    2. Имитация исполнения через заданную задержку
    3. Обновление виртуальных позиций
    4. Отправка событий в EventBus

    Attributes:
        _orders: Словарь активных ордеров {order_id: OrderInfo}.
        _positions: Словарь виртуальных позиций {instrument: Position}.
        _fill_delay: Задержка перед «исполнением» ордера (сек).
        _fill_probability: Вероятность исполнения за одну итерацию (0.0–1.0).
        _order_counter: Счётчик для генерации order_id.
    """

    DEFAULT_FILL_DELAY = 0.5         # Задержка исполнения (сек)
    DEFAULT_FILL_PROBABILITY = 1.0   # Вероятность исполнения
    DEFAULT_PARTIAL_FILL_RATIO = 0.5 # Доля частичного исполнения

    def __init__(self, event_bus: EventBus, config: Dict[str, Any]):
        """
        Инициализация симулятора ордеров.

        Args:
            event_bus: Шина событий.
            config: Конфигурация приложения.
        """
        self._event_bus = event_bus
        self._config = config

        # Хранилища
        self._orders: Dict[str, OrderInfo] = {}        # order_id → OrderInfo
        self._positions: Dict[str, Position] = {}      # instrument → Position
        self._order_history: List[OrderInfo] = []      # все ордера (история)
        self._order_counter: int = 0

        # Настройки симуляции
        sim_cfg = config.get("simulation", {})
        self._fill_delay = sim_cfg.get(
            "fill_delay", self.DEFAULT_FILL_DELAY
        )
        self._fill_probability = sim_cfg.get(
            "fill_probability", self.DEFAULT_FILL_PROBABILITY
        )

        self._connected: bool = False
        self._fill_task: Optional[asyncio.Task] = None

        logger.info(
            "SimulatedOrderProvider инициализирован: fill_delay=%.1fs, "
            "fill_probability=%.0f%%",
            self._fill_delay, self._fill_probability * 100,
        )

    # ─────────────────────────────────────────────────────────────────
    # Управление подключением
    # ─────────────────────────────────────────────────────────────────

    async def connect(self) -> bool:
        """Установить соединение (всегда успешно для симулятора)."""
        self._connected = True
        logger.info("SimulatedOrderProvider подключён")
        await self._event_bus.publish(
            EventType.PROVIDER_CONNECTED,
            {"provider": "simulated_orders", "timestamp": time.time()},
            source="SimulatedOrderProvider",
        )
        return True

    async def disconnect(self) -> None:
        """Закрыть соединение."""
        logger.info("Отключение SimulatedOrderProvider...")
        self._connected = False

        # Отменяем задачу обработки исполнения
        if self._fill_task and not self._fill_task.done():
            self._fill_task.cancel()
            try:
                await self._fill_task
            except asyncio.CancelledError:
                pass
            self._fill_task = None

        # Отменяем все активные ордера
        for order_id in list(self._orders.keys()):
            await self.cancel_order(order_id)

        await self._event_bus.publish(
            EventType.PROVIDER_DISCONNECTED,
            {"provider": "simulated_orders", "timestamp": time.time()},
            source="SimulatedOrderProvider",
        )
        logger.info("SimulatedOrderProvider отключён")

    async def is_connected(self) -> bool:
        """Проверить статус подключения."""
        return self._connected

    # ─────────────────────────────────────────────────────────────────
    # Торговые операции
    # ─────────────────────────────────────────────────────────────────

    async def place_order(self, request: OrderRequest) -> Optional[OrderInfo]:
        """
        Выставить лимитный ордер (симуляция).

        Ордер регистрируется в системе и «исполняется» асинхронно
        через заданную задержку (_fill_delay).

        Args:
            request: Параметры ордера.

        Returns:
            OrderInfo с присвоенным ID.
        """
        if not self._connected:
            logger.error("Симулятор не подключён, ордер отклонён")
            return None

        self._order_counter += 1
        order_id = f"SIM-{self._order_counter:06d}"

        order = OrderInfo(
            order_id=order_id,
            client_order_id=request.client_order_id,
            instrument=request.instrument,
            side=request.side,
            quantity=request.quantity,
            filled_quantity=0,
            price=request.price,
            avg_fill_price=0.0,
            status=OrderStatus.PENDING,
            comment=request.comment,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

        self._orders[order_id] = order

        logger.info(
            "Ордер размещён (SIM): id=%s, %s %s %d лот(а) по %.2f [%s]",
            order_id, request.side.value, request.instrument,
            request.quantity, request.price, request.comment or "-",
        )

        # Публикуем событие о выставлении ордера
        await self._event_bus.publish(
            EventType.ORDER_PLACED,
            {
                "order_id": order_id,
                "instrument": request.instrument,
                "side": request.side.value,
                "quantity": request.quantity,
                "price": request.price,
                "comment": request.comment,
            },
            source="SimulatedOrderProvider",
        )

        # Запускаем асинхронное «исполнение»
        asyncio.create_task(self._simulate_fill(order_id))

        return order

    async def modify_order(
        self, order_id: str, new_price: float, new_quantity: int
    ) -> Optional[OrderInfo]:
        """
        Изменить существующий ордер.

        В симуляции: если ордер ещё не исполнен — обновляем цену и количество.

        Args:
            order_id: ID ордера.
            new_price: Новая цена.
            new_quantity: Новое количество.

        Returns:
            Обновлённый OrderInfo или None.
        """
        order = self._orders.get(order_id)
        if not order:
            logger.warning("Ордер %s не найден для изменения", order_id)
            return None

        if order.status in (OrderStatus.FILLED, OrderStatus.CANCELLED):
            logger.warning(
                "Ордер %s уже в финальном статусе: %s",
                order_id, order.status.value,
            )
            return None

        old_price = order.price
        old_qty = order.quantity

        order.price = new_price
        order.quantity = new_quantity
        order.updated_at = datetime.now()

        logger.info(
            "Ордер изменён (SIM): id=%s, цена %.2f→%.2f, объём %d→%d",
            order_id, old_price, new_price, old_qty, new_quantity,
        )
        return order

    async def cancel_order(self, order_id: str) -> bool:
        """
        Отменить ордер.

        Args:
            order_id: ID ордера.

        Returns:
            True, если ордер найден и отменён.
        """
        order = self._orders.get(order_id)
        if not order:
            logger.warning("Ордер %s не найден для отмены", order_id)
            return False

        if order.status in (OrderStatus.FILLED, OrderStatus.CANCELLED):
            logger.debug(
                "Ордер %s уже в финальном статусе: %s",
                order_id, order.status.value,
            )
            return False

        order.status = OrderStatus.CANCELLED
        order.updated_at = datetime.now()
        self._order_history.append(order)

        logger.info(
            "Ордер отменён (SIM): id=%s, %s %s, было исполнено %d/%d",
            order_id, order.instrument, order.side.value,
            order.filled_quantity, order.quantity,
        )

        await self._event_bus.publish(
            EventType.ORDER_CANCELLED,
            {
                "order_id": order_id,
                "instrument": order.instrument,
                "side": order.side.value,
                "filled_quantity": order.filled_quantity,
                "requested_quantity": order.quantity,
            },
            source="SimulatedOrderProvider",
        )

        return True

    async def get_orders(self) -> List[OrderInfo]:
        """
        Получить список активных ордеров.

        Returns:
            Список OrderInfo только для ордеров в активном статусе.
        """
        return [
            order for order in self._orders.values()
            if order.status in (
                OrderStatus.PENDING,
                OrderStatus.ACTIVE,
                OrderStatus.PARTIALLY_FILLED,
            )
        ]

    async def get_positions(self) -> List[Position]:
        """
        Получить текущие виртуальные позиции.

        Returns:
            Список Position.
        """
        return list(self._positions.values())

    # ─────────────────────────────────────────────────────────────────
    # Симуляция исполнения
    # ─────────────────────────────────────────────────────────────────

    async def _simulate_fill(self, order_id: str) -> None:
        """
        Имитировать процесс исполнения ордера.

        Алгоритм:
        1. Ждать _fill_delay секунд (симуляция сетевой задержки).
        2. С вероятностью _fill_probability исполнить ордер.
        3. Если частичное исполнение — исполнить часть и запланировать остаток.
        4. Обновить виртуальную позицию.
        5. Опубликовать ORDER_FILLED или ORDER_PARTIAL_FILL.

        Args:
            order_id: ID ордера для «исполнения».
        """
        order = self._orders.get(order_id)
        if not order:
            return

        try:
            # Шаг 1: задержка (симуляция прохождения ордера до биржи)
            order.status = OrderStatus.ACTIVE
            await asyncio.sleep(self._fill_delay)

            # Проверяем, не отменён ли ордер за время ожидания
            if order.status == OrderStatus.CANCELLED:
                return

            # Шаг 2: определяем, исполняется ли ордер
            import random
            if random.random() > self._fill_probability:
                # Ордер не исполнен — остаётся активным
                logger.debug(
                    "Ордер %s не исполнен (симуляция вероятности)", order_id
                )
                return

            # Шаг 3: полное или частичное исполнение?
            remaining = order.quantity - order.filled_quantity
            if remaining <= 0:
                return

            # Симулируем частичное исполнение для 30% ордеров
            if remaining > 1 and random.random() < 0.3:
                # Частичное исполнение: случайная доля от остатка
                fill_qty = random.randint(1, max(1, remaining - 1))
                order.filled_quantity += fill_qty
                order.avg_fill_price = order.price  # В симуляции — по лимитной
                order.status = OrderStatus.PARTIALLY_FILLED
                order.updated_at = datetime.now()

                logger.info(
                    "Частичное исполнение (SIM): id=%s, +%d лот(а), "
                    "исполнено %d/%d по %.2f",
                    order_id, fill_qty, order.filled_quantity,
                    order.quantity, order.price,
                )

                await self._event_bus.publish(
                    EventType.ORDER_PARTIAL_FILL,
                    {
                        "order_id": order_id,
                        "instrument": order.instrument,
                        "side": order.side.value,
                        "filled_quantity": fill_qty,
                        "total_filled": order.filled_quantity,
                        "remaining": order.quantity - order.filled_quantity,
                        "price": order.price,
                    },
                    source="SimulatedOrderProvider",
                )

                # Планируем исполнение остатка
                asyncio.create_task(self._simulate_fill(order_id))
                return

            # Шаг 4: полное исполнение
            fill_qty = remaining
            order.filled_quantity = order.quantity
            order.avg_fill_price = order.price
            order.status = OrderStatus.FILLED
            order.updated_at = datetime.now()

            logger.info(
                "Исполнение (SIM): id=%s, %s %s %d лот(а) по %.2f",
                order_id, order.side.value, order.instrument,
                fill_qty, order.price,
            )

            # Обновляем виртуальную позицию
            self._update_position(order)

            # Переносим в историю
            self._order_history.append(order)

            await self._event_bus.publish(
                EventType.ORDER_FILLED,
                {
                    "order_id": order_id,
                    "instrument": order.instrument,
                    "side": order.side.value,
                    "quantity": fill_qty,
                    "price": order.price,
                    "comment": order.comment,
                },
                source="SimulatedOrderProvider",
            )

        except asyncio.CancelledError:
            logger.debug("Симуляция исполнения ордера %s прервана", order_id)
        except Exception as exc:
            logger.error(
                "Ошибка симуляции исполнения ордера %s: %s",
                order_id, exc, exc_info=True,
            )

    def _update_position(self, order: OrderInfo) -> None:
        """
        Обновить виртуальную позицию после исполнения ордера.

        Args:
            order: Исполненный ордер.
        """
        instrument = order.instrument
        side_multiplier = 1 if order.side == OrderSide.BUY else -1
        qty_change = order.filled_quantity * side_multiplier

        if instrument not in self._positions:
            self._positions[instrument] = Position(
                instrument=instrument,
                quantity=0,
                avg_price=0.0,
                current_price=order.price,
            )

        pos = self._positions[instrument]

        # Расчёт новой средней цены
        if (pos.quantity > 0 and qty_change > 0) or (pos.quantity < 0 and qty_change < 0):
            # Увеличение позиции — пересчитываем среднюю
            total_qty = abs(pos.quantity) + abs(qty_change)
            total_cost = abs(pos.quantity) * pos.avg_price + abs(qty_change) * order.price
            pos.avg_price = total_cost / total_qty if total_qty > 0 else 0.0
        elif pos.quantity == 0:
            # Новая позиция
            pos.avg_price = order.price
        # Иначе: уменьшение или переворот позиции — avg_price не меняется
        # (в реальной системе зависит от метода учёта: FIFO/LIFO/средняя)

        pos.quantity += qty_change
        pos.current_price = order.price

        # Если позиция закрылась
        if pos.quantity == 0:
            pos.avg_price = 0.0

        logger.info(
            "Позиция обновлена (SIM): %s, количество=%d, средняя=%.2f",
            instrument, pos.quantity, pos.avg_price,
        )

        # Публикуем событие
        asyncio.create_task(
            self._event_bus.publish(
                EventType.POSITION_UPDATED,
                {
                    "instrument": instrument,
                    "quantity": pos.quantity,
                    "avg_price": pos.avg_price,
                    "current_price": pos.current_price,
                },
                source="SimulatedOrderProvider",
            )
        )

    # ─────────────────────────────────────────────────────────────────
    # Служебные методы
    # ─────────────────────────────────────────────────────────────────

    def get_order_history(self) -> List[OrderInfo]:
        """Получить историю всех ордеров."""
        return list(self._order_history)

    def clear_history(self) -> None:
        """Очистить историю ордеров и позиций."""
        self._order_history.clear()
        self._positions.clear()
        self._orders.clear()
        self._order_counter = 0
        logger.info("История симулятора очищена")
