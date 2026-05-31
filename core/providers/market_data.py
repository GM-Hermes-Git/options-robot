"""
Абстрактные классы для провайдеров данных и ордеров.

Определяют контракты, которым должны следовать все реализации провайдеров.
Позволяет переключаться между MOEX ISS API и Alor API без изменения кода
стратегий и ордер-менеджера.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class OrderSide(Enum):
    """Направление ордера."""
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(Enum):
    """Статус ордера."""
    PENDING = "PENDING"        # Отправлен, ожидает подтверждения
    ACTIVE = "ACTIVE"           # Активен в стакане
    PARTIALLY_FILLED = "PARTIALLY_FILLED"  # Частично исполнен
    FILLED = "FILLED"           # Полностью исполнен
    CANCELLED = "CANCELLED"     # Отменён
    REJECTED = "REJECTED"       # Отклонён биржей


class OptionType(Enum):
    """Тип опциона."""
    CALL = "CALL"
    PUT = "PUT"


@dataclass
class Quote:
    """
    Котировка инструмента.

    Attributes:
        instrument: Тикер инструмента (например, 'Si-6.25M270625CA85000').
        bid: Лучшая цена покупки (0, если нет заявок).
        ask: Лучшая цена продажи (0, если нет заявок).
        last: Цена последней сделки (0, если не было).
        volume: Объём последней сделки (лоты).
        open_interest: Открытый интерес.
        timestamp: Время получения котировки.
        implied_volatility: Подразумеваемая волатильность (если доступна).
        theoretical_price: Теоретическая цена (если рассчитана).
    """
    instrument: str
    bid: float = 0.0
    ask: float = 0.0
    last: float = 0.0
    volume: int = 0
    open_interest: int = 0
    timestamp: datetime = field(default_factory=datetime.now)
    implied_volatility: Optional[float] = None
    theoretical_price: Optional[float] = None

    @property
    def mid(self) -> float:
        """Средняя цена между bid и ask. 0, если стакан пуст."""
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        return self.last or 0.0

    @property
    def spread(self) -> float:
        """Спред (в пунктах цены)."""
        if self.bid > 0 and self.ask > 0:
            return self.ask - self.bid
        return 0.0

    @property
    def spread_pct(self) -> float:
        """Спред в процентах от mid-цены."""
        mid = self.mid
        if mid > 0:
            return (self.spread / mid) * 100
        return 0.0


@dataclass
class OrderRequest:
    """
    Заявка на выставление ордера.

    Attributes:
        instrument: Тикер инструмента.
        side: Направление (BUY/SELL).
        quantity: Количество (в лотах).
        price: Лимитная цена.
        comment: Комментарий к ордеру (ID стратегии и т.п.).
        client_order_id: Пользовательский ID для трекинга.
    """
    instrument: str
    side: OrderSide
    quantity: int
    price: float
    comment: str = ""
    client_order_id: str = ""


@dataclass
class OrderInfo:
    """
    Информация об ордере (ответ от провайдера).

    Attributes:
        order_id: ID ордера, присвоенный биржей/брокером.
        client_order_id: Пользовательский ID (из OrderRequest).
        instrument: Тикер инструмента.
        side: Направление.
        quantity: Запрошенное количество.
        filled_quantity: Исполненное количество.
        price: Лимитная цена.
        avg_fill_price: Средняя цена исполнения.
        status: Статус ордера.
        comment: Комментарий.
        created_at: Время создания.
        updated_at: Время последнего обновления.
    """
    order_id: str
    client_order_id: str = ""
    instrument: str = ""
    side: OrderSide = OrderSide.BUY
    quantity: int = 0
    filled_quantity: int = 0
    price: float = 0.0
    avg_fill_price: float = 0.0
    status: OrderStatus = OrderStatus.PENDING
    comment: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class Position:
    """
    Позиция по инструменту.

    Attributes:
        instrument: Тикер инструмента.
        quantity: Количество (положительное = long, отрицательное = short).
        avg_price: Средняя цена входа.
        current_price: Текущая рыночная цена.
        unrealized_pnl: Нереализованный P&L.
        realized_pnl: Реализованный P&L.
    """
    instrument: str
    quantity: int = 0
    avg_price: float = 0.0
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0


@dataclass
class OptionInstrument:
    """
    Параметры опционного контракта.

    Attributes:
        ticker: Тикер опциона (например, 'Si-6.25M270625CA85000').
        base_asset: Базовый актив (тикер фьючерса, например 'Si').
        option_type: Тип опциона (CALL/PUT).
        strike: Страйк (цена исполнения).
        expiration_date: Дата экспирации.
        lot_size: Размер лота (количество единиц БА).
        last_price: Цена последней сделки.
        open_interest: Открытый интерес.
    """
    ticker: str
    base_asset: str
    option_type: OptionType
    strike: float
    expiration_date: datetime
    lot_size: int = 1
    last_price: float = 0.0
    open_interest: int = 0


class MarketDataProvider(ABC):
    """
    Абстрактный провайдер рыночных данных.

    Определяет интерфейс для получения котировок, списков инструментов,
    параметров контрактов и волатильности. Конкретные реализации:
    MoexDataProvider (MOEX ISS API) и AlorDataProvider (Alor Open API).
    """

    @abstractmethod
    async def connect(self) -> bool:
        """
        Установить соединение с источником данных.

        Returns:
            True, если подключение успешно.
        """
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Закрыть соединение с источником данных."""
        ...

    @abstractmethod
    async def is_connected(self) -> bool:
        """
        Проверить статус подключения.

        Returns:
            True, если соединение активно.
        """
        ...

    @abstractmethod
    async def get_quote(self, instrument: str) -> Optional[Quote]:
        """
        Получить котировку по одному инструменту.

        Args:
            instrument: Тикер инструмента.

        Returns:
            Объект Quote или None, если данные недоступны.
        """
        ...

    @abstractmethod
    async def get_quotes(self, instruments: List[str]) -> Dict[str, Quote]:
        """
        Получить котировки по нескольким инструментам.

        Args:
            instruments: Список тикеров.

        Returns:
            Словарь {тикер: Quote}. Инструменты без данных отсутствуют в словаре.
        """
        ...

    @abstractmethod
    async def get_option_chain(
        self,
        base_asset: str,
        expiration_date: Optional[datetime] = None,
    ) -> List[OptionInstrument]:
        """
        Получить опционную доску по базовому активу.

        Args:
            base_asset: Тикер базового актива (фьючерса).
            expiration_date: Фильтр по дате экспирации (None = все доступные).

        Returns:
            Список доступных опционных контрактов.
        """
        ...

    @abstractmethod
    async def get_futures_price(self, base_asset: str) -> Optional[float]:
        """
        Получить текущую цену фьючерса (базового актива).

        Args:
            base_asset: Тикер фьючерса (например, 'Si').

        Returns:
            Цена последней сделки или None.
        """
        ...

    @abstractmethod
    async def subscribe_quotes(self, instruments: List[str]) -> bool:
        """
        Подписаться на потоковые обновления котировок.

        При каждом обновлении должен отправляться event QUOTE_UPDATED
        через EventBus.

        Args:
            instruments: Список тикеров для подписки.

        Returns:
            True, если подписка успешна.
        """
        ...

    @abstractmethod
    async def unsubscribe_quotes(self, instruments: List[str]) -> None:
        """
        Отписаться от обновлений котировок.

        Args:
            instruments: Список тикеров для отписки.
        """
        ...


class OrderProvider(ABC):
    """
    Абстрактный провайдер торговых операций.

    Определяет интерфейс для выставления, изменения, отмены ордеров
    и получения позиций. Конкретные реализации:
    SimulatedOrderProvider (симуляция) и AlorOrderProvider (боевой).
    """

    @abstractmethod
    async def connect(self) -> bool:
        """
        Установить соединение с торговой системой.

        Returns:
            True, если подключение успешно.
        """
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Закрыть соединение с торговой системой."""
        ...

    @abstractmethod
    async def is_connected(self) -> bool:
        """
        Проверить статус подключения.

        Returns:
            True, если соединение активно.
        """
        ...

    @abstractmethod
    async def place_order(self, request: OrderRequest) -> Optional[OrderInfo]:
        """
        Выставить лимитный ордер.

        Args:
            request: Параметры ордера (OrderRequest).

        Returns:
            OrderInfo с ID ордера и статусом, или None при ошибке.
        """
        ...

    @abstractmethod
    async def modify_order(
        self, order_id: str, new_price: float, new_quantity: int
    ) -> Optional[OrderInfo]:
        """
        Изменить (переставить) существующий ордер.

        Args:
            order_id: ID ордера для изменения.
            new_price: Новая лимитная цена.
            new_quantity: Новое количество.

        Returns:
            Обновлённый OrderInfo или None при ошибке.
        """
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """
        Отменить ордер.

        Args:
            order_id: ID ордера для отмены.

        Returns:
            True, если ордер успешно отменён.
        """
        ...

    @abstractmethod
    async def get_orders(self) -> List[OrderInfo]:
        """
        Получить список активных ордеров.

        Returns:
            Список OrderInfo.
        """
        ...

    @abstractmethod
    async def get_positions(self) -> List[Position]:
        """
        Получить текущие позиции по счёту.

        Returns:
            Список Position.
        """
        ...
