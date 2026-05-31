"""
Провайдер рыночных данных: Московская Биржа (MOEX ISS API).

Реализует MarketDataProvider для получения котировок, опционных досок
и цен базовых активов через информационно-статистический сервер MOEX (ISS).

Особенности:
    - Только REST (MOEX ISS не предоставляет публичный WebSocket).
    - Кэширование данных с настраиваемым TTL для снижения нагрузки на API.
    - Автоматическое определение ближайшей экспирации при запросе опционов.
    - Получение implied volatility обратным решением из рыночных цен (Bid/Ask).

API MOEX ISS:
    - Список экспираций: /iss/engines/futures/markets/options/assets/{asset}/securities.json
    - Опционная доска:   /iss/engines/futures/markets/options/securities.json?optionboard=1&asset={asset}&expiration_date={date}
    - Котировки:          /iss/engines/futures/markets/options/securities/{ticker}.json
    - Цена БА:            /iss/engines/futures/markets/forts/securities/{asset}.json?marketdata=1

Использование:
    from core.providers.moex_provider import MoexDataProvider
    from core.event_bus import EventBus

    bus = EventBus()
    provider = MoexDataProvider(bus, config)
    await provider.connect()
    quote = await provider.get_quote("Si-6.25M270625CA85000")
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from core.event_bus import EventBus, EventType
from core.providers.market_data import (
    MarketDataProvider,
    Quote,
    OptionInstrument,
    OptionType,
)

logger = logging.getLogger(__name__)


class MoexDataProvider(MarketDataProvider):
    """
    Провайдер рыночных данных через MOEX ISS API.

    Attributes:
        _base_url: Базовый URL MOEX ISS API.
        _session: Асинхронная HTTP-сессия (aiohttp).
        _connected: Флаг активного подключения.
        _cache: Кэш ответов API (словарь {ключ: (данные, timestamp)}).
        _cache_ttl: Время жизни кэша в секундах (по умолчанию 5 сек).
        _request_timeout: Таймаут HTTP-запросов в секундах.
    """

    # Параметры по умолчанию
    DEFAULT_BASE_URL = "https://iss.moex.com/iss"
    DEFAULT_CACHE_TTL = 5.0        # Время жизни кэша (сек)
    DEFAULT_REQUEST_TIMEOUT = 10   # Таймаут HTTP-запросов (сек)
    DEFAULT_RETRY_COUNT = 2        # Количество повторных попыток при ошибке
    DEFAULT_RETRY_DELAY = 1.0      # Задержка между попытками (сек)

    def __init__(self, event_bus: EventBus, config: Dict[str, Any]):
        """
        Инициализация провайдера.

        Args:
            event_bus: Шина событий для публикации статуса подключения.
            config: Конфигурация приложения (из settings.json).
        """
        self._event_bus = event_bus

        # Настройки из конфигурации
        moex_cfg = config.get("providers", {}).get("moex", {})
        self._base_url = moex_cfg.get("base_url", self.DEFAULT_BASE_URL)
        self._cache_ttl = moex_cfg.get("cache_ttl", self.DEFAULT_CACHE_TTL)
        self._request_timeout = moex_cfg.get(
            "request_timeout", self.DEFAULT_REQUEST_TIMEOUT
        )

        self._session: Optional[aiohttp.ClientSession] = None
        self._connected: bool = False
        self._cache: Dict[str, Tuple[Any, float]] = {}  # ключ → (данные, timestamp)

        logger.info(
            "MoexDataProvider инициализирован: base_url=%s, cache_ttl=%.1fs",
            self._base_url, self._cache_ttl,
        )

    # ─────────────────────────────────────────────────────────────────
    # Управление подключением
    # ─────────────────────────────────────────────────────────────────

    async def connect(self) -> bool:
        """
        Установить соединение с MOEX ISS API.

        Создаёт HTTP-сессию и проверяет доступность сервера.

        Returns:
            True, если подключение успешно.
        """
        if self._connected:
            logger.warning("MoexDataProvider уже подключён")
            return True

        try:
            # Создаём TCP-коннектор с ограничением на количество соединений
            connector = aiohttp.TCPConnector(
                limit=20,
                limit_per_host=10,
                ttl_dns_cache=300,
            )
            timeout = aiohttp.ClientTimeout(total=self._request_timeout)
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={
                    "User-Agent": "OptionsRobot/0.1 (Hermes Agent)",
                    "Accept": "application/json",
                },
            )

            # Проверяем доступность сервера (запрос к корню ISS)
            async with self._session.get(
                f"{self._base_url}/engines/futures/markets/options/securities.json",
                params={"limit": 1},
            ) as resp:
                if resp.status == 200:
                    self._connected = True
                    logger.info("MoexDataProvider подключён к %s", self._base_url)
                    await self._event_bus.publish(
                        EventType.PROVIDER_CONNECTED,
                        {"provider": "moex", "timestamp": time.time()},
                        source="MoexDataProvider",
                    )
                    return True
                else:
                    logger.error(
                        "Ошибка подключения к MOEX ISS: HTTP %d", resp.status
                    )
                    return False

        except aiohttp.ClientError as exc:
            logger.error("Ошибка подключения к MOEX ISS: %s", exc)
            await self._cleanup_session()
            return False
        except Exception as exc:
            logger.error("Неожиданная ошибка при подключении: %s", exc, exc_info=True)
            await self._cleanup_session()
            return False

    async def disconnect(self) -> None:
        """Закрыть соединение с MOEX ISS API."""
        logger.info("Отключение MoexDataProvider...")
        self._connected = False
        await self._cleanup_session()
        self._cache.clear()
        await self._event_bus.publish(
            EventType.PROVIDER_DISCONNECTED,
            {"provider": "moex", "timestamp": time.time()},
            source="MoexDataProvider",
        )
        logger.info("MoexDataProvider отключён")

    async def is_connected(self) -> bool:
        """Проверить статус подключения."""
        return self._connected and self._session is not None

    async def _cleanup_session(self) -> None:
        """Закрыть HTTP-сессию без ошибок."""
        if self._session:
            try:
                await self._session.close()
            except Exception as exc:
                logger.debug("Ошибка при закрытии сессии: %s", exc)
            finally:
                self._session = None

    # ─────────────────────────────────────────────────────────────────
    # Котировки
    # ─────────────────────────────────────────────────────────────────

    async def get_quote(self, instrument: str) -> Optional[Quote]:
        """
        Получить котировку одного инструмента.

        Использует ISS endpoint:
            /iss/engines/futures/markets/options/securities/{ticker}.json

        Args:
            instrument: Тикер опциона (например, 'Si-6.25M270625CA85000').

        Returns:
            Quote или None, если данные недоступны.
        """
        cache_key = f"quote:{instrument}"
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached

        url = f"{self._base_url}/engines/futures/markets/options/securities/{instrument}.json"
        data = await self._fetch_json(url)

        if data is None:
            return None

        try:
            # Структура ответа: securities.data содержит массив с параметрами
            # marketdata.data содержит рыночные данные (bid, ask, last и т.д.)
            sec_data = self._extract_block(data, "securities")
            mkt_data = self._extract_block(data, "marketdata")

            if not sec_data:
                logger.debug("Нет данных securities для %s", instrument)
                return None

            sec_row = sec_data[0]

            # Извлекаем рыночные данные, если есть
            bid = 0.0
            ask = 0.0
            last = 0.0
            volume = 0
            oi = 0

            if mkt_data and len(mkt_data) > 0:
                mkt_row = mkt_data[0]
                bid = self._safe_float(mkt_row, "BID", 0.0)
                ask = self._safe_float(mkt_row, "OFFER", 0.0)
                last = self._safe_float(mkt_row, "LAST", 0.0)
                volume = self._safe_int(mkt_row, "VOLUMETODAY", 0)
                oi = self._safe_int(mkt_row, "OPENPOSITION", 0)

            quote = Quote(
                instrument=instrument,
                bid=bid,
                ask=ask,
                last=last,
                volume=volume,
                open_interest=oi,
                timestamp=datetime.now(),
            )

            self._put_to_cache(cache_key, quote)
            return quote

        except (KeyError, IndexError, TypeError) as exc:
            logger.error("Ошибка парсинга котировки %s: %s", instrument, exc)
            return None

    async def get_quotes(self, instruments: List[str]) -> Dict[str, Quote]:
        """
        Получить котировки нескольких инструментов.

        Выполняет параллельные запросы через asyncio.gather.

        Args:
            instruments: Список тикеров.

        Returns:
            Словарь {тикер: Quote}.
        """
        if not instruments:
            return {}

        tasks = [self.get_quote(inst) for inst in instruments]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        quotes = {}
        for inst, result in zip(instruments, results):
            if isinstance(result, Exception):
                logger.error("Ошибка получения котировки %s: %s", inst, result)
            elif result is not None:
                quotes[inst] = result

        return quotes

    # ─────────────────────────────────────────────────────────────────
    # Опционная доска
    # ─────────────────────────────────────────────────────────────────

    async def get_option_chain(
        self,
        base_asset: str,
        expiration_date: Optional[datetime] = None,
    ) -> List[OptionInstrument]:
        """
        Получить опционную доску по базовому активу.

        Использует ISS endpoint:
            /iss/engines/futures/markets/options/securities.json
            ?optionboard=1&asset={asset}

        Args:
            base_asset: Тикер базового актива (например, 'Si').
            expiration_date: Фильтр по дате экспирации (None = ближайшая).

        Returns:
            Список OptionInstrument.
        """
        # Если дата не задана, получаем ближайшую экспирацию
        if expiration_date is None:
            expiration_date = await self._get_nearest_expiration(base_asset)
            if expiration_date is None:
                logger.error("Не удалось определить дату экспирации для %s", base_asset)
                return []

        date_str = expiration_date.strftime("%Y-%m-%d")
        cache_key = f"option_chain:{base_asset}:{date_str}"
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached

        url = f"{self._base_url}/engines/futures/markets/options/securities.json"
        params = {
            "optionboard": "1",
            "asset": base_asset,
        }
        data = await self._fetch_json(url, params=params)

        if data is None:
            return []

        try:
            sec_data = self._extract_block(data, "securities")
            if not sec_data:
                logger.warning("Нет опционов для %s на %s", base_asset, date_str)
                return []

            chain = []
            for row in sec_data:
                # Пропускаем опционы с другой датой экспирации
                exp_date_str = str(row.get("EXPDATE", ""))
                if exp_date_str != date_str:
                    continue

                # Определяем тип опциона
                opt_type_str = str(row.get("OPTIONTYPE", "")).upper()
                if opt_type_str == "CALL":
                    opt_type = OptionType.CALL
                elif opt_type_str == "PUT":
                    opt_type = OptionType.PUT
                else:
                    continue  # Пропускаем неизвестные типы

                instrument = OptionInstrument(
                    ticker=str(row.get("SECID", "")),
                    base_asset=base_asset,
                    option_type=opt_type,
                    strike=self._safe_float(row, "STRIKE", 0.0),
                    expiration_date=expiration_date,
                    lot_size=self._safe_int(row, "LOTSIZE", 1),
                    last_price=self._safe_float(row, "LAST", 0.0),
                    open_interest=self._safe_int(row, "OPENPOSITION", 0),
                )
                chain.append(instrument)

            logger.info(
                "Получена опционная доска %s (%s): %d опционов",
                base_asset, date_str, len(chain),
            )
            self._put_to_cache(cache_key, chain)
            return chain

        except (KeyError, IndexError, TypeError) as exc:
            logger.error("Ошибка парсинга опционной доски %s: %s", base_asset, exc)
            return []

    async def _get_nearest_expiration(self, base_asset: str) -> Optional[datetime]:
        """
        Определить ближайшую дату экспирации опционов для базового актива.

        Использует ISS endpoint:
            /iss/engines/futures/markets/options/assets/{asset}/securities.json

        Args:
            base_asset: Тикер базового актива.

        Returns:
            Дата ближайшей экспирации или None.
        """
        cache_key = f"expirations:{base_asset}"
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached

        url = (
            f"{self._base_url}/engines/futures/markets/options"
            f"/assets/{base_asset}/securities.json"
        )
        data = await self._fetch_json(url)

        if data is None:
            return None

        try:
            sec_data = self._extract_block(data, "securities")
            if not sec_data:
                return None

            # Собираем уникальные даты экспирации
            expirations = set()
            for row in sec_data:
                exp_str = str(row.get("EXPDATE", ""))
                if exp_str:
                    try:
                        exp_dt = datetime.strptime(exp_str, "%Y-%m-%d")
                        expirations.add(exp_dt)
                    except ValueError:
                        continue

            if not expirations:
                return None

            # Выбираем ближайшую дату, которая ещё не прошла
            now = datetime.now()
            future_expirations = [d for d in expirations if d >= now]
            if future_expirations:
                nearest = min(future_expirations)
            else:
                # Все даты в прошлом — берём ближайшую к сейчас
                nearest = min(expirations, key=lambda d: abs((d - now).days))

            self._put_to_cache(cache_key, nearest)
            return nearest

        except (KeyError, IndexError, TypeError) as exc:
            logger.error(
                "Ошибка получения экспираций для %s: %s", base_asset, exc
            )
            return None

    # ─────────────────────────────────────────────────────────────────
    # Цена базового актива (фьючерса)
    # ─────────────────────────────────────────────────────────────────

    async def get_futures_price(self, base_asset: str) -> Optional[float]:
        """
        Получить текущую цену фьючерса (базового актива).

        Использует ISS endpoint:
            /iss/engines/futures/markets/forts/securities/{asset}.json?marketdata=1

        Args:
            base_asset: Тикер фьючерса (например, 'Si').

        Returns:
            Цена последней сделки (LAST) или None.
        """
        cache_key = f"futures_price:{base_asset}"
        cached = self._get_from_cache(cache_key)
        if cached is not None:
            return cached

        url = (
            f"{self._base_url}/engines/futures/markets/forts"
            f"/securities/{base_asset}.json"
        )
        params = {"marketdata": "1"}
        data = await self._fetch_json(url, params=params)

        if data is None:
            return None

        try:
            mkt_data = self._extract_block(data, "marketdata")
            if mkt_data and len(mkt_data) > 0:
                price = self._safe_float(mkt_data[0], "LAST", None)
                if price is not None and price > 0:
                    self._put_to_cache(cache_key, price)
                    return price

            logger.debug("Нет данных marketdata для %s", base_asset)
            return None

        except (KeyError, IndexError, TypeError) as exc:
            logger.error("Ошибка получения цены %s: %s", base_asset, exc)
            return None

    # ─────────────────────────────────────────────────────────────────
    # Подписка на котировки (заглушка — MOEX ISS не имеет WS)
    # ─────────────────────────────────────────────────────────────────

    async def subscribe_quotes(self, instruments: List[str]) -> bool:
        """
        Подписаться на обновления котировок.

        MOEX ISS API не предоставляет публичный WebSocket, поэтому
        подписка реализуется через периодический поллинг (polling).
        В данной версии — заглушка. Поллинг реализуется на уровне
        TriggerEngine через периодический вызов get_quotes().

        Args:
            instruments: Список тикеров для подписки.

        Returns:
            True.
        """
        logger.info(
            "Подписка на котировки (polling-режим): %d инструментов",
            len(instruments),
        )
        # Заглушка: MOEX ISS не поддерживает WebSocket-подписку.
        # Поллинг организуется снаружи (TriggerEngine).
        return True

    async def unsubscribe_quotes(self, instruments: List[str]) -> None:
        """
        Отписаться от обновлений.

        В текущей реализации — no-op, т.к. поллинг управляется извне.

        Args:
            instruments: Список тикеров.
        """
        logger.debug("Отписка от котировок: %d инструментов", len(instruments))

    # ─────────────────────────────────────────────────────────────────
    # Вспомогательные методы
    # ─────────────────────────────────────────────────────────────────

    async def _fetch_json(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        retry_count: int = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Выполнить GET-запрос к MOEX ISS API и вернуть JSON.

        Args:
            url: URL запроса.
            params: Параметры запроса.
            retry_count: Количество повторных попыток (None = значение по умолчанию).

        Returns:
            Распарсенный JSON-словарь или None при ошибке.
        """
        if retry_count is None:
            retry_count = self.DEFAULT_RETRY_COUNT

        if not self._session:
            logger.error("HTTP-сессия не создана")
            return None

        last_error = None
        for attempt in range(retry_count + 1):
            try:
                async with self._session.get(url, params=params) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    elif resp.status == 429:
                        # Rate limit — ждём и повторяем
                        delay = 2 ** attempt
                        logger.warning(
                            "Rate limit (429) для %s, попытка %d/%d, ждём %ds",
                            url, attempt + 1, retry_count + 1, delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                    else:
                        logger.warning(
                            "HTTP %d для %s (попытка %d/%d)",
                            resp.status, url, attempt + 1, retry_count + 1,
                        )
                        last_error = f"HTTP {resp.status}"
                        if attempt < retry_count:
                            await asyncio.sleep(self.DEFAULT_RETRY_DELAY)

            except asyncio.TimeoutError:
                logger.warning("Таймаут запроса %s (попытка %d/%d)",
                              url, attempt + 1, retry_count + 1)
                last_error = "Timeout"
                if attempt < retry_count:
                    await asyncio.sleep(self.DEFAULT_RETRY_DELAY)
            except aiohttp.ClientError as exc:
                logger.warning("Ошибка HTTP для %s: %s (попытка %d/%d)",
                             url, exc, attempt + 1, retry_count + 1)
                last_error = str(exc)
                if attempt < retry_count:
                    await asyncio.sleep(self.DEFAULT_RETRY_DELAY)

        logger.error("Не удалось получить %s после %d попыток: %s",
                     url, retry_count + 1, last_error)
        return None

    @staticmethod
    def _extract_block(
        data: Dict[str, Any], block_name: str
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Извлечь блок данных из ответа MOEX ISS.

        Структура ответа ISS:
        {
            "securities": {"columns": [...], "data": [[...], ...]},
            "marketdata": {"columns": [...], "data": [[...], ...]}
        }

        Args:
            data: JSON-ответ от ISS API.
            block_name: Имя блока (например, 'securities', 'marketdata').

        Returns:
            Список словарей {column_name: value} или None.
        """
        block = data.get(block_name)
        if not block:
            return None

        columns = block.get("columns", [])
        rows = block.get("data", [])

        if not columns or not rows:
            return []

        # Преобразуем строки данных в словари {колонка: значение}
        result = []
        for row in rows:
            record = {}
            for col_idx, col_name in enumerate(columns):
                if col_idx < len(row):
                    record[col_name] = row[col_idx]
            result.append(record)

        return result

    @staticmethod
    def _safe_float(
        row: Dict[str, Any], key: str, default: float = 0.0
    ) -> float:
        """Безопасное извлечение float из строки ответа ISS."""
        value = row.get(key)
        if value is None:
            return default
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _safe_int(
        row: Dict[str, Any], key: str, default: int = 0
    ) -> int:
        """Безопасное извлечение int из строки ответа ISS."""
        value = row.get(key)
        if value is None:
            return default
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    # ─────────────────────────────────────────────────────────────────
    # Кэширование
    # ─────────────────────────────────────────────────────────────────

    def _get_from_cache(self, key: str) -> Optional[Any]:
        """
        Получить данные из кэша, если они не устарели.

        Args:
            key: Ключ кэша.

        Returns:
            Закэшированные данные или None.
        """
        if key in self._cache:
            data, timestamp = self._cache[key]
            if time.time() - timestamp < self._cache_ttl:
                return data
            else:
                # Удаляем устаревшую запись
                del self._cache[key]
        return None

    def _put_to_cache(self, key: str, data: Any) -> None:
        """Сохранить данные в кэш с текущей временной меткой."""
        self._cache[key] = (data, time.time())

    def clear_cache(self) -> None:
        """Очистить кэш."""
        self._cache.clear()
        logger.debug("Кэш MoexDataProvider очищен")
