"""
Модуль расчёта греков и теоретической цены опционов.

Реализует модель Блэка-76 (Black-76) для опционов на фьючерсы:
    - Теоретическая цена Call и Put
    - Греки: Дельта (Δ), Гамма (Γ), Тета (Θ), Вега (V)
    - Подразумеваемая волатильность (IV) — обратное решение из рыночной цены
    - Расчёт P&L позиции

Модель Блэка-76 (Black Model for Futures Options):
    Базовая формула для европейских опционов на фьючерсы.

    d1 = [ln(F/K) + (σ²/2)T] / (σ√T)
    d2 = d1 - σ√T

    Call = e^(-rT) × [F × N(d1) - K × N(d2)]
    Put  = e^(-rT) × [K × N(-d2) - F × N(-d1)]

    Δ_call = e^(-rT) × N(d1)
    Δ_put  = e^(-rT) × [N(d1) - 1]
    Γ = e^(-rT) × n(d1) / (F × σ × √T)
    Θ = -(F × σ × e^(-rT) × n(d1)) / (2√T) - rF×e^(-rT)×N(d1) + rK×e^(-rT)×N(d2)
        (для Call; для Put аналогично)
    V = F × e^(-rT) × √T × n(d1)

    где:
        F  — цена фьючерса (базового актива)
        K  — страйк опциона
        T  — время до экспирации (в годах)
        r  — безрисковая процентная ставка
        σ  — волатильность (в десятичных долях, например 0.20 для 20%)
        N() — кумулятивная функция нормального распределения
        n() — плотность нормального распределения

Использование:
    from core.greeks_engine import GreeksEngine, Black76Result

    engine = GreeksEngine(risk_free_rate=0.085)
    result = engine.calculate(
        futures_price=65000.0,
        strike=64000.0,
        time_to_expiry=0.25,  # 3 месяца
        volatility=0.20,
        option_type=OptionType.CALL,
    )
    print(f"Цена: {result.price:.2f}, Δ={result.delta:.4f}, IV={result.vega:.4f}")
"""

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple

import numpy as np
from scipy.stats import norm

from core.providers.market_data import OptionType

logger = logging.getLogger(__name__)


@dataclass
class Black76Result:
    """
    Результат расчёта модели Блэка-76.

    Attributes:
        price: Теоретическая цена опциона.
        delta: Дельта — чувствительность цены к изменению цены БА.
        gamma: Гамма — чувствительность дельты к изменению цены БА.
        theta: Тета — чувствительность цены к течению времени (годовая).
        theta_daily: Тета, нормированная на один день (theta / 365).
        vega: Вега — чувствительность цены к изменению волатильности на 1%.
        rho: Ро — чувствительность цены к изменению безрисковой ставки.
        d1: Промежуточное значение d1.
        d2: Промежуточное значение d2.
        forward: Цена фьючерса (F).
        strike: Страйк (K).
        time_to_expiry: Время до экспирации в годах.
        volatility: Волатильность (σ).
        risk_free_rate: Безрисковая ставка (r).
        option_type: Тип опциона (CALL/PUT).
        calculation_time: Время расчёта.
    """
    price: float
    delta: float
    gamma: float
    theta: float
    theta_daily: float
    vega: float
    rho: float
    d1: float
    d2: float
    forward: float
    strike: float
    time_to_expiry: float
    volatility: float
    risk_free_rate: float
    option_type: OptionType
    calculation_time: datetime = None

    def __post_init__(self):
        if self.calculation_time is None:
            self.calculation_time = datetime.now()


class GreeksEngine:
    """
    Движок расчёта греков и теоретической цены опционов по модели Блэка-76.

    Поддерживает два режима работы (согласно ТЗ, раздел 7.1):
        1. Ручная волатильность — пользователь задаёт σ явно.
        2. Волатильность из стакана — IV выводится обратным решением
           из рыночных цен (Bid/Ask/Mid).

    Attributes:
        risk_free_rate: Безрисковая процентная ставка (годовая, в долях).
                         По умолчанию 8.5% (0.085).
        iv_precision: Точность расчёта IV (сходимость, по умолчанию 1e-6).
        iv_max_iterations: Максимальное количество итераций для поиска IV.
        iv_initial_guess: Начальное приближение для поиска IV.
    """

    # Константы
    TRADING_DAYS_PER_YEAR = 365  # Для пересчёта годовой теты в дневную

    def __init__(
        self,
        risk_free_rate: float = 0.085,
        iv_precision: float = 1e-6,
        iv_max_iterations: int = 100,
        iv_initial_guess: float = 0.30,
    ):
        """
        Инициализация движка греков.

        Args:
            risk_free_rate: Безрисковая ставка (годовая, в долях).
            iv_precision: Точность расчёта IV.
            iv_max_iterations: Максимум итераций для поиска IV.
            iv_initial_guess: Начальное приближение IV (30% годовых).
        """
        self.risk_free_rate = risk_free_rate
        self.iv_precision = iv_precision
        self.iv_max_iterations = iv_max_iterations
        self.iv_initial_guess = iv_initial_guess

        logger.info(
            "GreeksEngine инициализирован: r=%.2f%%, IV precision=%.0e, "
            "max iterations=%d",
            risk_free_rate * 100, iv_precision, iv_max_iterations,
        )

    # ─────────────────────────────────────────────────────────────────
    # Основной расчёт
    # ─────────────────────────────────────────────────────────────────

    def calculate(
        self,
        futures_price: float,
        strike: float,
        time_to_expiry: float,
        volatility: float,
        option_type: OptionType,
    ) -> Black76Result:
        """
        Рассчитать теоретическую цену и все греки опциона по модели Блэка-76.

        Args:
            futures_price: Текущая цена фьючерса (F).
            strike: Страйк опциона (K).
            time_to_expiry: Время до экспирации в годах (T).
                            Например, 0.25 для 3 месяцев.
            volatility: Волатильность в десятичных долях (σ).
                        Например, 0.20 для 20%.
            option_type: Тип опциона (CALL или PUT).

        Returns:
            Black76Result с ценой и всеми греками.

        Raises:
            ValueError: Если входные параметры некорректны (F ≤ 0, K ≤ 0, T ≤ 0, σ ≤ 0).
        """
        # Валидация входных параметров
        self._validate_inputs(futures_price, strike, time_to_expiry, volatility)

        F = futures_price
        K = strike
        T = time_to_expiry
        σ = volatility
        r = self.risk_free_rate

        # Расчёт d1 и d2
        if T <= 0 or σ <= 0:
            # Граничные случаи: нулевое время или волатильность
            return self._calculate_boundary(F, K, T, r, option_type)

        sqrt_T = math.sqrt(T)
        σ_sqrt_T = σ * sqrt_T

        d1 = (math.log(F / K) + (σ ** 2 / 2) * T) / σ_sqrt_T
        d2 = d1 - σ_sqrt_T

        # Нормальное распределение
        N_d1 = norm.cdf(d1)   # N(d1)
        N_d2 = norm.cdf(d2)   # N(d2)
        N_neg_d1 = norm.cdf(-d1)  # N(-d1)
        N_neg_d2 = norm.cdf(-d2)  # N(-d2)
        n_d1 = norm.pdf(d1)   # n(d1) — плотность

        # Дисконтирующий множитель
        discount = math.exp(-r * T)

        # ── Теоретическая цена ──
        if option_type == OptionType.CALL:
            price = discount * (F * N_d1 - K * N_d2)
        else:
            price = discount * (K * N_neg_d2 - F * N_neg_d1)

        # ── Греки ──
        # Дельта (Δ): чувствительность цены к изменению цены БА на 1 пункт
        if option_type == OptionType.CALL:
            delta = discount * N_d1
        else:
            delta = discount * (N_d1 - 1)

        # Гамма (Γ): изменение дельты при изменении цены БА на 1 пункт
        # Одинакова для Call и Put
        gamma = discount * n_d1 / (F * σ_sqrt_T)

        # Вега (V): изменение цены при изменении σ на 1% (0.01)
        # В формуле — на единицу σ; делим на 100 для представления «на 1%»
        vega = F * discount * sqrt_T * n_d1 / 100.0

        # Тета (Θ): изменение цены при уменьшении T на 1 год
        # Для опционов на фьючерсы Блэка-76:
        theta_term1 = -(F * σ * discount * n_d1) / (2 * sqrt_T)

        if option_type == OptionType.CALL:
            theta_term2 = -r * F * discount * N_d1 + r * K * discount * N_d2
        else:
            theta_term2 = r * F * discount * N_neg_d1 - r * K * discount * N_neg_d2

        theta = theta_term1 + theta_term2
        theta_daily = theta / self.TRADING_DAYS_PER_YEAR

        # Ро (ρ): изменение цены при изменении r на 1% (0.01)
        if option_type == OptionType.CALL:
            rho = discount * T * (K * N_d2 - F * N_d1) / 100.0
        else:
            rho = discount * T * (F * N_neg_d1 - K * N_neg_d2) / 100.0

        return Black76Result(
            price=max(price, 0.0),  # Цена не может быть отрицательной
            delta=delta,
            gamma=gamma,
            theta=theta,
            theta_daily=theta_daily,
            vega=vega,
            rho=rho,
            d1=d1,
            d2=d2,
            forward=F,
            strike=K,
            time_to_expiry=T,
            volatility=σ,
            risk_free_rate=r,
            option_type=option_type,
        )

    # ─────────────────────────────────────────────────────────────────
    # Подразумеваемая волатильность (IV)
    # ─────────────────────────────────────────────────────────────────

    def implied_volatility(
        self,
        market_price: float,
        futures_price: float,
        strike: float,
        time_to_expiry: float,
        option_type: OptionType,
        initial_guess: Optional[float] = None,
    ) -> Optional[float]:
        """
        Рассчитать подразумеваемую волатильность (IV) из рыночной цены опциона.

        Использует метод Ньютона-Рафсона (Newton-Raphson) для обратного
        решения уравнения Блэка-76 относительно σ.

        Алгоритм:
            1. Начальное приближение σ₀.
            2. Итерация: σ_{n+1} = σ_n - (Price(σ_n) - MarketPrice) / Vega(σ_n)
            3. Повторять до сходимости (|diff| < precision) или исчерпания итераций.

        Args:
            market_price: Рыночная цена опциона.
            futures_price: Цена фьючерса (F).
            strike: Страйк (K).
            time_to_expiry: Время до экспирации (T, в годах).
            option_type: Тип опциона (CALL/PUT).
            initial_guess: Начальное приближение σ (None = использовать по умолчанию).

        Returns:
            Подразумеваемая волатильность (σ) в десятичных долях,
            или None, если решение не найдено.
        """
        if market_price <= 0:
            logger.debug("IV: рыночная цена <= 0 (%.4f), возвращаем None", market_price)
            return None

        if initial_guess is None:
            initial_guess = self.iv_initial_guess

        σ = initial_guess
        vega_scale = 100.0  # vega в нашей реализации на 1%, нужно на 1 единицу σ

        for iteration in range(self.iv_max_iterations):
            # Рассчитываем цену и вегу при текущем σ
            result = self.calculate(futures_price, strike, time_to_expiry, σ, option_type)

            price_diff = result.price - market_price
            vega_unit = result.vega * vega_scale  # vega на единицу σ (не на 1%)

            if abs(price_diff) < self.iv_precision:
                logger.debug("IV найдена: σ=%.4f%% за %d итераций",
                            σ * 100, iteration + 1)
                return σ

            if abs(vega_unit) < 1e-12:
                # Вега близка к нулю — метод Ньютона не сработает
                # Переключаемся на бинарный поиск
                logger.debug("IV: вега ~ 0 на итерации %d, переключаемся на бисекцию",
                            iteration + 1)
                return self._implied_volatility_bisection(
                    market_price, futures_price, strike,
                    time_to_expiry, option_type,
                )

            # Шаг Ньютона-Рафсона
            σ_new = σ - price_diff / vega_unit

            # Защита от выхода за разумные границы
            if σ_new <= 0.001:  # Минимум 0.1%
                σ_new = 0.001
            elif σ_new > 5.0:   # Максимум 500%
                σ_new = 5.0

            σ = σ_new

        logger.warning(
            "IV не сошлась за %d итераций (цена=%.4f, F=%.2f, K=%.2f, T=%.4f)",
            self.iv_max_iterations, market_price, futures_price, strike, time_to_expiry,
        )
        return None

    def _implied_volatility_bisection(
        self,
        market_price: float,
        futures_price: float,
        strike: float,
        time_to_expiry: float,
        option_type: OptionType,
    ) -> Optional[float]:
        """
        Поиск IV методом бисекции (бинарного поиска).

        Резервный метод, когда метод Ньютона не сходится (например, при веге ~0).

        Args:
            market_price: Рыночная цена.
            futures_price: Цена фьючерса.
            strike: Страйк.
            time_to_expiry: Время до экспирации.
            option_type: Тип опциона.

        Returns:
            IV или None.
        """
        σ_low = 0.001   # 0.1%
        σ_high = 5.0    # 500%

        # Проверяем, что цена при σ_low и σ_high охватывает market_price
        price_low = self.calculate(
            futures_price, strike, time_to_expiry, σ_low, option_type
        ).price
        price_high = self.calculate(
            futures_price, strike, time_to_expiry, σ_high, option_type
        ).price

        if market_price < min(price_low, price_high) or market_price > max(price_low, price_high):
            logger.debug(
                "IV bisection: цена %.4f вне диапазона [%.4f, %.4f]",
                market_price, price_low, price_high,
            )
            return None

        for iteration in range(self.iv_max_iterations):
            σ_mid = (σ_low + σ_high) / 2
            price_mid = self.calculate(
                futures_price, strike, time_to_expiry, σ_mid, option_type
            ).price

            if abs(price_mid - market_price) < self.iv_precision:
                logger.debug("IV bisection: σ=%.4f%% за %d итераций",
                            σ_mid * 100, iteration + 1)
                return σ_mid

            if price_mid < market_price:
                σ_low = σ_mid
            else:
                σ_high = σ_mid

        σ_mid = (σ_low + σ_high) / 2
        logger.debug("IV bisection: σ=%.4f%% (приближённо, %d итераций)",
                    σ_mid * 100, self.iv_max_iterations)
        return σ_mid

    # ─────────────────────────────────────────────────────────────────
    # P&L позиции
    # ─────────────────────────────────────────────────────────────────

    def calculate_position_pnl(
        self,
        legs: list,  # list of dict: {option_type, strike, quantity, sign, iv_at_open, ...}
        futures_price_now: float,
        futures_price_open: float,
        time_to_expiry: float,
    ) -> Tuple[float, float]:
        """
        Рассчитать P&L позиции (абсолютный и в процентах).

        Согласно ТЗ (раздел 7.3):
            P&L = тек. теор. стоимость - теор. стоимость на момент открытия.
            P&L % = P&L / |стоимость на открытии| × 100%.

        Args:
            legs: Список «ног» позиции. Каждая нога — словарь с ключами:
                  - option_type: OptionType (CALL/PUT)
                  - strike: страйк (float)
                  - quantity: количество контрактов (int)
                  - sign: направление (+1 для Buy, -1 для Sell)
                  - iv_at_open: волатильность на момент открытия (float)
                  - lot_size: размер лота (int, по умолчанию 1)
            futures_price_now: Текущая цена фьючерса.
            futures_price_open: Цена фьючерса на момент открытия позиции.
            time_to_expiry: Время до экспирации (годы).

        Returns:
            Кортеж (pnl_absolute, pnl_percent).
        """
        theoretical_value_now = 0.0
        theoretical_value_open = 0.0

        for leg in legs:
            opt_type = leg["option_type"]
            strike = leg["strike"]
            quantity = leg["quantity"]
            sign = leg["sign"]
            iv_open = leg.get("iv_at_open", self.iv_initial_guess)
            lot_size = leg.get("lot_size", 1)

            # Текущая теор. стоимость (по текущей IV из стакана или расчётной)
            current_iv = leg.get("iv_current", iv_open)
            result_now = self.calculate(
                futures_price_now, strike, time_to_expiry, current_iv, opt_type,
            )
            theoretical_value_now += (
                result_now.price * quantity * sign * lot_size
            )

            # Теор. стоимость на момент открытия
            result_open = self.calculate(
                futures_price_open, strike, time_to_expiry, iv_open, opt_type,
            )
            theoretical_value_open += (
                result_open.price * quantity * sign * lot_size
            )

        pnl_absolute = theoretical_value_now - theoretical_value_open
        pnl_percent = (
            (pnl_absolute / abs(theoretical_value_open) * 100.0)
            if abs(theoretical_value_open) > 0
            else 0.0
        )

        return pnl_absolute, pnl_percent

    # ─────────────────────────────────────────────────────────────────
    # Вспомогательные методы
    # ─────────────────────────────────────────────────────────────────

    @staticmethod
    def time_to_expiry_from_dates(
        expiration_date: datetime, current_time: Optional[datetime] = None
    ) -> float:
        """
        Рассчитать время до экспирации в годах.

        Args:
            expiration_date: Дата экспирации опциона.
            current_time: Текущее время (None = datetime.now()).

        Returns:
            Время в годах (например, 0.25 для 3 месяцев).
        """
        if current_time is None:
            current_time = datetime.now()

        delta = expiration_date - current_time
        # Учитываем, что опционы экспирируются в 18:45 MSK в день экспирации
        # Для упрощения — просто календарные дни / 365
        years = delta.total_seconds() / (365.25 * 24 * 3600)
        return max(years, 0.0)  # Не может быть отрицательным

    @staticmethod
    def _validate_inputs(
        futures_price: float,
        strike: float,
        time_to_expiry: float,
        volatility: float,
    ) -> None:
        """
        Проверить корректность входных параметров.

        Raises:
            ValueError: Если параметры некорректны.
        """
        if futures_price <= 0:
            raise ValueError(f"Цена фьючерса должна быть > 0, получено: {futures_price}")
        if strike <= 0:
            raise ValueError(f"Страйк должен быть > 0, получено: {strike}")
        if time_to_expiry < 0:
            raise ValueError(
                f"Время до экспирации не может быть отрицательным: {time_to_expiry}"
            )
        if volatility <= 0:
            raise ValueError(
                f"Волатильность должна быть > 0, получено: {volatility}"
            )

    def _calculate_boundary(
        self,
        F: float,
        K: float,
        T: float,
        r: float,
        option_type: OptionType,
    ) -> Black76Result:
        """
        Расчёт для граничных случаев (T <= 0 или σ = 0).

        При T ≤ 0: опцион на экспирации, цена = внутренняя стоимость.
        При σ = 0: опцион ведёт себя как форвард.

        Args:
            F: Цена фьючерса.
            K: Страйк.
            T: Время до экспирации.
            r: Безрисковая ставка.
            option_type: Тип опциона.

        Returns:
            Black76Result.
        """
        discount = math.exp(-r * max(T, 0))

        if T <= 0:
            # Экспирация: цена = внутренняя стоимость
            if option_type == OptionType.CALL:
                price = max(F - K, 0.0)
                delta = 1.0 if F > K else 0.0
            else:
                price = max(K - F, 0.0)
                delta = -1.0 if F < K else 0.0
        else:
            # Нулевая волатильность
            if option_type == OptionType.CALL:
                price = max(discount * (F - K), 0.0)
                delta = discount if F > K else 0.0
            else:
                price = max(discount * (K - F), 0.0)
                delta = -discount if F < K else 0.0

        return Black76Result(
            price=price,
            delta=delta,
            gamma=0.0,
            theta=0.0,
            theta_daily=0.0,
            vega=0.0,
            rho=0.0,
            d1=0.0,
            d2=0.0,
            forward=F,
            strike=K,
            time_to_expiry=T,
            volatility=0.0,
            risk_free_rate=r,
            option_type=option_type,
        )

    def update_risk_free_rate(self, new_rate: float) -> None:
        """
        Обновить безрисковую ставку.

        Args:
            new_rate: Новая ставка в десятичных долях (например, 0.085 для 8.5%).
        """
        old_rate = self.risk_free_rate
        self.risk_free_rate = new_rate
        logger.info(
            "Безрисковая ставка изменена: %.2f%% → %.2f%%",
            old_rate * 100, new_rate * 100,
        )
