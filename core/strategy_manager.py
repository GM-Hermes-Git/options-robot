"""
Управление стратегиями (StrategyManager).

Отвечает за жизненный цикл стратегий: создание, запуск, остановка, мониторинг.
Хранит параметры стратегий и их текущий статус.

Статусы стратегии (из ТЗ, раздел 4.3):
    CONFIGURED    — параметры заданы, стратегия не запущена
    WAITING       — запущена, ожидает наступления даты/времени начала
    ACTIVE        — мониторинг цены БА, ожидание триггера
    TRIGGERED     — триггер сработал, ордера выставлены в стакан
    BUILDING      — идёт набор позиции (частичное исполнение)
    POSITION_OPEN — позиция набрана полностью, хеджер активен
    CLOSING       — идёт закрытие позиции (SL/TP или ручное)
    STOPPED       — стратегия остановлена

Версия для Этапа 1: ЗАГЛУШКА.
Полная реализация — Этап 2 (Ядро стратегий).
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from core.event_bus import Event, EventType

from core.providers.market_data import OptionType

logger = logging.getLogger(__name__)


class StrategyStatus(Enum):
    """Статусы жизненного цикла стратегии."""
    CONFIGURED = "CONFIGURED"
    WAITING = "WAITING"
    ACTIVE = "ACTIVE"
    TRIGGERED = "TRIGGERED"
    BUILDING = "BUILDING"
    POSITION_OPEN = "POSITION_OPEN"
    CLOSING = "CLOSING"
    STOPPED = "STOPPED"


@dataclass
class Leg:
    """Нога стратегии — один опционный контракт в составе стратегии.

    Attributes:
        leg_index: Порядковый номер ноги (0-based), определяет очерёдность исполнения.
        option_type: Тип опциона (CALL/PUT).
        strike: Страйк (цена исполнения).
        sign: +1 = Buy, -1 = Sell.
        quantity: Количество контрактов.
        iv_mode: "manual" или "market" — откуда брать волатильность.
        manual_iv: Волатильность, если iv_mode="manual".
        iv_multiplier: Множитель к рыночной IV (по умолчанию 1.0).
    """

    leg_index: int
    option_type: OptionType
    strike: float
    sign: int
    quantity: int
    iv_mode: str = "market"
    manual_iv: Optional[float] = None
    iv_multiplier: float = 1.0

    def __post_init__(self) -> None:
        if self.sign not in (1, -1):
            raise ValueError(f"sign must be +1 or -1, got {self.sign}")
        if self.quantity <= 0:
            raise ValueError(f"quantity must be positive, got {self.quantity}")
        if self.leg_index < 0:
            raise ValueError(f"leg_index must be >= 0, got {self.leg_index}")

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация ноги в словарь."""
        return {
            "leg_index": self.leg_index,
            "option_type": self.option_type.value,
            "strike": self.strike,
            "sign": self.sign,
            "quantity": self.quantity,
            "iv_mode": self.iv_mode,
            "manual_iv": self.manual_iv,
            "iv_multiplier": self.iv_multiplier,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Leg":
        """Десериализация ноги из словаря."""
        return Leg(
            leg_index=data["leg_index"],
            option_type=OptionType(data["option_type"]),
            strike=data["strike"],
            sign=data["sign"],
            quantity=data["quantity"],
            iv_mode=data.get("iv_mode", "market"),
            manual_iv=data.get("manual_iv"),
            iv_multiplier=data.get("iv_multiplier", 1.0),
        )


@dataclass
class StrategyDefinition:
    """Описание параметров стратегии.

    Attributes:
        strategy_id: Уникальный идентификатор (0 для новой, БД присвоит реальный).
        name: Название стратегии (например "Si straddle").
        base_asset: Тикер фьючерса (например "Si").
        status: Текущий статус стратегии.
        legs: Список ног стратегии.
        trigger_level: Цена БА, при которой активируется стратегия.
        trigger_deactivation_threshold: Порог деактивации триггера.
        start_time: Время начала активности (None = немедленно).
        end_time: Время окончания (None = бессрочно).
        max_contracts_per_leg: Макс. кол-во контрактов на одну ногу.
        sl_percent: Стоп-лосс в % (по умолчанию 50.0).
        tp_percent: Тейк-профит в % (по умолчанию 100.0).
        created_at: Время создания.
        updated_at: Время последнего обновления.
    """

    strategy_id: int = 0
    name: str = ""
    base_asset: str = ""
    status: StrategyStatus = StrategyStatus.CONFIGURED
    legs: List[Leg] = field(default_factory=list)
    trigger_level: float = 0.0
    trigger_deactivation_threshold: float = 0.0
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    max_contracts_per_leg: int = 1
    sl_percent: float = 50.0
    tp_percent: float = 100.0
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name must not be empty")
        if not self.base_asset:
            raise ValueError("base_asset must not be empty")
        if self.trigger_level <= 0:
            raise ValueError(f"trigger_level must be positive, got {self.trigger_level}")
        if not self.legs:
            raise ValueError("legs must not be empty")

    def to_dict(self) -> Dict[str, Any]:
        """Сериализация стратегии в словарь."""
        return {
            "strategy_id": self.strategy_id,
            "name": self.name,
            "base_asset": self.base_asset,
            "status": self.status.value,
            "legs": [leg.to_dict() for leg in self.legs],
            "trigger_level": self.trigger_level,
            "trigger_deactivation_threshold": self.trigger_deactivation_threshold,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "max_contracts_per_leg": self.max_contracts_per_leg,
            "sl_percent": self.sl_percent,
            "tp_percent": self.tp_percent,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "StrategyDefinition":
        """Десериализация стратегии из словаря."""
        legs_data = data.get("legs", [])
        legs = [Leg.from_dict(leg) for leg in legs_data]

        created_at = data.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)

        updated_at = data.get("updated_at")
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at)

        start_time = data.get("start_time")
        if isinstance(start_time, str):
            start_time = datetime.fromisoformat(start_time)

        end_time = data.get("end_time")
        if isinstance(end_time, str):
            end_time = datetime.fromisoformat(end_time)

        return StrategyDefinition(
            strategy_id=data.get("strategy_id", 0),
            name=data["name"],
            base_asset=data["base_asset"],
            status=StrategyStatus(data.get("status", "CONFIGURED")),
            legs=legs,
            trigger_level=data["trigger_level"],
            trigger_deactivation_threshold=data.get("trigger_deactivation_threshold", 0.0),
            start_time=start_time,
            end_time=end_time,
            max_contracts_per_leg=data.get("max_contracts_per_leg", 1),
            sl_percent=data.get("sl_percent", 50.0),
            tp_percent=data.get("tp_percent", 100.0),
            created_at=created_at or datetime.now(),
            updated_at=updated_at or datetime.now(),
        )

    @staticmethod
    def to_legs_json(legs: List[Leg]) -> str:
        """Сериализация списка ног в JSON-строку для хранения в БД."""
        return json.dumps([leg.to_dict() for leg in legs])

    @staticmethod
    def from_legs_json(json_str: str) -> List[Leg]:
        """Десериализация списка ног из JSON-строки."""
        data = json.loads(json_str)
        return [Leg.from_dict(item) for item in data]


class StrategyManager:
    """
    Менеджер стратегий — управление жизненным циклом стратегий.

    Отвечает за:
        - Создание, запуск, остановку стратегий
        - Переходы между статусами (CONFIGURED → WAITING → ACTIVE → TRIGGERED → ...)
        - Обработку событий TRIGGER_FIRED, ORDER_FILLED, ORDER_PARTIAL_FILL, POSITION_UPDATED
        - Загрузку/сохранение конфигураций в БД
    """

    def __init__(
        self,
        event_bus: "EventBus",
        db_manager: "DatabaseManager",
        config: dict,
    ):
        self._event_bus = event_bus
        self._db_manager = db_manager
        self._config = config
        self._strategies: Dict[int, StrategyDefinition] = {}
        # Отслеживание заполненных ног для каждой стратегии: {strategy_id: set(leg_indices)}
        self._filled_legs: Dict[int, set] = {}
        # Фоновая задача периодической проверки WAITING → ACTIVE
        self._check_task: Optional[asyncio.Task] = None
        # Интервал проверки start_time (сек)
        self._check_interval: int = 10

        # Подписка на события
        self._event_bus.subscribe(
            EventType.TRIGGER_FIRED, self._on_trigger_fired, priority=20
        )
        self._event_bus.subscribe(
            EventType.ORDER_FILLED, self._on_order_filled, priority=20
        )
        self._event_bus.subscribe(
            EventType.ORDER_PARTIAL_FILL, self._on_order_filled, priority=20
        )
        self._event_bus.subscribe(
            EventType.POSITION_UPDATED, self._on_position_updated, priority=20
        )

        logger.info("StrategyManager инициализирован")

    # ──────────────────────────────────────────────
    # Публичные методы
    # ──────────────────────────────────────────────

    async def initialize(self) -> None:
        """Загрузить все стратегии из БД.

        Для стратегий в статусе WAITING проверяет, не наступил ли start_time.
        Запускает фоновую задачу периодической проверки WAITING → ACTIVE.
        """
        strategies = await self._db_manager.load_all_strategies()
        for s in strategies:
            self._strategies[s.strategy_id] = s
            # Если start_time уже наступил, переводим сразу в ACTIVE
            if (
                s.status == StrategyStatus.WAITING
                and s.start_time is not None
                and s.start_time <= datetime.now()
            ):
                s.status = StrategyStatus.ACTIVE
                s.updated_at = datetime.now()
                await self._db_manager.save_strategy(s)

        # Запуск периодической проверки start_time
        self._check_task = asyncio.create_task(self._check_start_times())

        logger.info(
            "StrategyManager инициализирован: загружено %d стратегий",
            len(strategies),
        )

    async def create_strategy(self, definition: StrategyDefinition) -> int:
        """Создать новую стратегию.

        Args:
            definition: Параметры стратегии (strategy_id будет проигнорирован).

        Returns:
            ID созданной стратегии.

        Raises:
            ValueError: Если определение невалидно (проверка в __post_init__).
        """
        # Создаём копию с strategy_id=0 для вставки в БД,
        # чтобы не мутировать оригинальный объект
        from dataclasses import replace

        def_copy = replace(definition, strategy_id=0)
        strategy_id = await self._db_manager.save_strategy(def_copy)
        # Сохраняем копию с правильным ID во внутреннем словаре
        stored = replace(def_copy, strategy_id=strategy_id)
        self._strategies[strategy_id] = stored

        logger.info(
            "Создана стратегия #%d: '%s' (%s)",
            strategy_id,
            definition.name,
            definition.base_asset,
        )
        return strategy_id

    async def start_strategy(self, strategy_id: int) -> bool:
        """Запустить стратегию.

        Переходы:
            CONFIGURED → WAITING (если start_time в будущем)
            CONFIGURED → ACTIVE (если start_time=None или уже прошёл)

        Args:
            strategy_id: ID стратегии.

        Returns:
            True, если статус изменён; False, если стратегия не найдена
            или не в статусе CONFIGURED.
        """
        strategy = self._strategies.get(strategy_id)
        if strategy is None or strategy.status != StrategyStatus.CONFIGURED:
            return False

        now = datetime.now()
        if strategy.start_time is not None and strategy.start_time > now:
            strategy.status = StrategyStatus.WAITING
            logger.info(
                "Стратегия #%d переведена в WAITING (start_time=%s)",
                strategy_id,
                strategy.start_time,
            )
        else:
            strategy.status = StrategyStatus.ACTIVE
            logger.info("Стратегия #%d переведена в ACTIVE", strategy_id)

        strategy.updated_at = now
        await self._db_manager.save_strategy(strategy)
        return True

    async def stop_strategy(self, strategy_id: int) -> bool:
        """Остановить стратегию.

        Любой статус → STOPPED (кроме уже STOPPED).
        Публикует событие STRATEGY_STOPPED.

        Args:
            strategy_id: ID стратегии.

        Returns:
            True, если стратегия остановлена; False, если не найдена
            или уже в статусе STOPPED.
        """
        strategy = self._strategies.get(strategy_id)
        if strategy is None or strategy.status == StrategyStatus.STOPPED:
            return False

        strategy.status = StrategyStatus.STOPPED
        strategy.updated_at = datetime.now()
        await self._db_manager.save_strategy(strategy)

        await self._event_bus.publish(
            EventType.STRATEGY_STOPPED,
            {"strategy_id": strategy_id},
            source="strategy_manager",
        )

        logger.info("Стратегия #%d остановлена", strategy_id)
        return True

    def get_strategy(self, strategy_id: int) -> Optional[StrategyDefinition]:
        """Получить стратегию по ID.

        Args:
            strategy_id: ID стратегии.

        Returns:
            StrategyDefinition или None, если не найдена.
        """
        return self._strategies.get(strategy_id)

    def get_all_strategies(self) -> List[StrategyDefinition]:
        """Получить все стратегии.

        Returns:
            Список всех стратегий.
        """
        return list(self._strategies.values())

    def get_strategies_by_status(
        self, status: StrategyStatus
    ) -> List[StrategyDefinition]:
        """Получить стратегии по статусу.

        Args:
            status: Фильтруемый статус.

        Returns:
            Список стратегий с указанным статусом.
        """
        return [s for s in self._strategies.values() if s.status == status]

    # ──────────────────────────────────────────────
    # Обработчики событий
    # ──────────────────────────────────────────────

    async def _on_trigger_fired(self, event: Event) -> None:
        """Обработчик TRIGGER_FIRED.

        Если стратегия в статусе ACTIVE → переводит в TRIGGERED.
        """
        strategy_id = event.data.get("strategy_id")
        if strategy_id is None:
            return

        strategy = self._strategies.get(strategy_id)
        if strategy is None:
            return

        if strategy.status != StrategyStatus.ACTIVE:
            return

        strategy.status = StrategyStatus.TRIGGERED
        strategy.updated_at = datetime.now()
        await self._db_manager.save_strategy(strategy)

        logger.info(
            "Стратегия #%d: TRIGGER_FIRED → TRIGGERED", strategy_id
        )

    async def _on_order_filled(self, event: Event) -> None:
        """Обработчик ORDER_FILLED и ORDER_PARTIAL_FILL.

        Переходы:
            TRIGGERED → BUILDING (первый заполненный ордер)
            BUILDING → POSITION_OPEN (все ноги заполнены)
        """
        strategy_id = event.data.get("strategy_id")
        leg_index = event.data.get("leg_index")
        if strategy_id is None or leg_index is None:
            return

        strategy = self._strategies.get(strategy_id)
        if strategy is None:
            return

        if strategy.status == StrategyStatus.TRIGGERED:
            # Первое заполнение → BUILDING
            strategy.status = StrategyStatus.BUILDING
            strategy.updated_at = datetime.now()
            # Инициализируем отслеживание заполненных ног
            self._filled_legs[strategy_id] = {leg_index}
            await self._db_manager.save_strategy(strategy)
            logger.info(
                "Стратегия #%d: ордер исполнен (нога %d) → BUILDING",
                strategy_id,
                leg_index,
            )

        elif strategy.status == StrategyStatus.BUILDING:
            # Отмечаем ногу как заполненную
            if strategy_id not in self._filled_legs:
                self._filled_legs[strategy_id] = set()
            self._filled_legs[strategy_id].add(leg_index)

            # Проверяем, все ли ноги заполнены
            if len(self._filled_legs[strategy_id]) >= len(strategy.legs):
                strategy.status = StrategyStatus.POSITION_OPEN
                strategy.updated_at = datetime.now()
                await self._db_manager.save_strategy(strategy)
                logger.info(
                    "Стратегия #%d: все ноги заполнены → POSITION_OPEN",
                    strategy_id,
                )

    async def _on_position_updated(self, event: Event) -> None:
        """Обработчик POSITION_UPDATED.

        Если стратегия в POSITION_OPEN и позиция закрыта (closed=True) →
        переводит в STOPPED.
        """
        strategy_id = event.data.get("strategy_id")
        if strategy_id is None:
            return

        strategy = self._strategies.get(strategy_id)
        if strategy is None:
            return

        if strategy.status != StrategyStatus.POSITION_OPEN:
            return

        is_closed = event.data.get("closed", False)
        if is_closed:
            strategy.status = StrategyStatus.STOPPED
            strategy.updated_at = datetime.now()
            await self._db_manager.save_strategy(strategy)

            await self._event_bus.publish(
                EventType.STRATEGY_STOPPED,
                {"strategy_id": strategy_id},
                source="strategy_manager",
            )

            logger.info(
                "Стратегия #%d: позиция закрыта → STOPPED", strategy_id
            )

    # ──────────────────────────────────────────────
    # Внутренние методы
    # ──────────────────────────────────────────────

    async def _check_start_times(self) -> None:
        """Периодическая проверка WAITING → ACTIVE.

        Запускается в фоновой задаче. Проверяет каждые _check_interval секунд,
        не наступил ли start_time для стратегий в статусе WAITING.
        """
        while True:
            await asyncio.sleep(self._check_interval)
            await self._check_start_times_once()

    async def _check_start_times_once(self) -> None:
        """Однократная проверка WAITING → ACTIVE (для тестов и внутреннего вызова)."""
        now = datetime.now()
        for strategy in list(self._strategies.values()):
            if (
                strategy.status == StrategyStatus.WAITING
                and strategy.start_time is not None
                and strategy.start_time <= now
            ):
                strategy.status = StrategyStatus.ACTIVE
                strategy.updated_at = now
                await self._db_manager.save_strategy(strategy)
                logger.info(
                    "Стратегия #%d: start_time наступил → ACTIVE",
                    strategy.strategy_id,
                )
