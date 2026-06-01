"""Тесты для StrategyDefinition и Leg."""

import json
from datetime import datetime

import pytest

from core.strategy_manager import Leg, StrategyDefinition, StrategyStatus
from core.providers.market_data import OptionType


class TestLeg:
    """Тесты для dataclass Leg."""

    def test_create_valid_leg(self):
        """Создание Leg с валидными параметрами."""
        leg = Leg(
            leg_index=0,
            option_type=OptionType.CALL,
            strike=85000.0,
            sign=1,
            quantity=1,
        )
        assert leg.leg_index == 0
        assert leg.option_type == OptionType.CALL
        assert leg.strike == 85000.0
        assert leg.sign == 1
        assert leg.quantity == 1
        assert leg.iv_mode == "market"
        assert leg.manual_iv is None
        assert leg.iv_multiplier == 1.0

    def test_create_leg_sell(self):
        """Создание Leg с sign=-1 (продажа)."""
        leg = Leg(
            leg_index=1,
            option_type=OptionType.PUT,
            strike=82000.0,
            sign=-1,
            quantity=2,
            iv_mode="manual",
            manual_iv=25.0,
            iv_multiplier=1.5,
        )
        assert leg.sign == -1
        assert leg.iv_mode == "manual"
        assert leg.manual_iv == 25.0
        assert leg.iv_multiplier == 1.5

    def test_invalid_sign_raises(self):
        """sign должен быть +1 или -1."""
        with pytest.raises(ValueError, match="sign must be"):
            Leg(
                leg_index=0,
                option_type=OptionType.CALL,
                strike=85000.0,
                sign=2,
                quantity=1,
            )

    def test_invalid_sign_zero_raises(self):
        """sign = 0 вызывает ValueError."""
        with pytest.raises(ValueError, match="sign must be"):
            Leg(
                leg_index=0,
                option_type=OptionType.CALL,
                strike=85000.0,
                sign=0,
                quantity=1,
            )

    def test_invalid_quantity_raises(self):
        """quantity <= 0 вызывает ValueError."""
        with pytest.raises(ValueError, match="quantity must be"):
            Leg(
                leg_index=0,
                option_type=OptionType.CALL,
                strike=85000.0,
                sign=1,
                quantity=0,
            )

    def test_negative_quantity_raises(self):
        """quantity < 0 вызывает ValueError."""
        with pytest.raises(ValueError, match="quantity must be"):
            Leg(
                leg_index=0,
                option_type=OptionType.CALL,
                strike=85000.0,
                sign=1,
                quantity=-1,
            )

    def test_invalid_leg_index_raises(self):
        """leg_index < 0 вызывает ValueError."""
        with pytest.raises(ValueError, match="leg_index must be"):
            Leg(
                leg_index=-1,
                option_type=OptionType.CALL,
                strike=85000.0,
                sign=1,
                quantity=1,
            )

    def test_to_dict(self):
        """Сериализация Leg в dict."""
        leg = Leg(
            leg_index=0,
            option_type=OptionType.CALL,
            strike=85000.0,
            sign=1,
            quantity=3,
            iv_mode="manual",
            manual_iv=30.0,
            iv_multiplier=0.8,
        )
        d = leg.to_dict()
        assert d["leg_index"] == 0
        assert d["option_type"] == "CALL"
        assert d["strike"] == 85000.0
        assert d["sign"] == 1
        assert d["quantity"] == 3
        assert d["iv_mode"] == "manual"
        assert d["manual_iv"] == 30.0
        assert d["iv_multiplier"] == 0.8

    def test_from_dict(self):
        """Десериализация Leg из dict."""
        data = {
            "leg_index": 1,
            "option_type": "PUT",
            "strike": 90000.0,
            "sign": -1,
            "quantity": 5,
            "iv_mode": "market",
            "manual_iv": None,
            "iv_multiplier": 1.0,
        }
        leg = Leg.from_dict(data)
        assert leg.leg_index == 1
        assert leg.option_type == OptionType.PUT
        assert leg.strike == 90000.0
        assert leg.sign == -1
        assert leg.quantity == 5
        assert leg.iv_mode == "market"
        assert leg.manual_iv is None


class TestStrategyDefinition:
    """Тесты для dataclass StrategyDefinition."""

    def test_create_minimal_strategy(self):
        """Создание стратегии с минимальными параметрами."""
        legs = [
            Leg(leg_index=0, option_type=OptionType.CALL, strike=85000.0, sign=1, quantity=1),
            Leg(leg_index=1, option_type=OptionType.PUT, strike=82000.0, sign=1, quantity=1),
        ]
        strategy = StrategyDefinition(
            name="Si straddle",
            base_asset="Si",
            legs=legs,
            trigger_level=84000.0,
        )
        assert strategy.strategy_id == 0
        assert strategy.name == "Si straddle"
        assert strategy.base_asset == "Si"
        assert len(strategy.legs) == 2
        assert strategy.trigger_level == 84000.0
        assert strategy.trigger_deactivation_threshold == 0.0
        assert strategy.start_time is None
        assert strategy.end_time is None
        assert strategy.max_contracts_per_leg == 1
        assert strategy.sl_percent == 50.0
        assert strategy.tp_percent == 100.0
        assert strategy.status == StrategyStatus.CONFIGURED
        assert isinstance(strategy.created_at, datetime)
        assert isinstance(strategy.updated_at, datetime)

    def test_empty_name_raises(self):
        """name не может быть пустым."""
        with pytest.raises(ValueError, match="name must not be empty"):
            StrategyDefinition(
                name="",
                base_asset="Si",
                legs=[Leg(leg_index=0, option_type=OptionType.CALL, strike=85000.0, sign=1, quantity=1)],
                trigger_level=84000.0,
            )

    def test_empty_base_asset_raises(self):
        """base_asset не может быть пустым."""
        with pytest.raises(ValueError, match="base_asset must not be empty"):
            StrategyDefinition(
                name="test",
                base_asset="",
                legs=[Leg(leg_index=0, option_type=OptionType.CALL, strike=85000.0, sign=1, quantity=1)],
                trigger_level=84000.0,
            )

    def test_trigger_level_zero_raises(self):
        """trigger_level <= 0 вызывает ValueError."""
        with pytest.raises(ValueError, match="trigger_level must be positive"):
            StrategyDefinition(
                name="test",
                base_asset="Si",
                legs=[Leg(leg_index=0, option_type=OptionType.CALL, strike=85000.0, sign=1, quantity=1)],
                trigger_level=0,
            )

    def test_trigger_level_negative_raises(self):
        """trigger_level < 0 вызывает ValueError."""
        with pytest.raises(ValueError, match="trigger_level must be positive"):
            StrategyDefinition(
                name="test",
                base_asset="Si",
                legs=[Leg(leg_index=0, option_type=OptionType.CALL, strike=85000.0, sign=1, quantity=1)],
                trigger_level=-100,
            )

    def test_empty_legs_raises(self):
        """legs не может быть пустым списком."""
        with pytest.raises(ValueError, match="legs must not be empty"):
            StrategyDefinition(
                name="test",
                base_asset="Si",
                legs=[],
                trigger_level=84000.0,
            )

    def test_to_dict_full(self):
        """Сериализация StrategyDefinition с полными данными."""
        legs = [
            Leg(leg_index=0, option_type=OptionType.CALL, strike=85000.0, sign=1, quantity=1),
        ]
        strat = StrategyDefinition(
            strategy_id=5,
            name="test_strat",
            base_asset="Si",
            status=StrategyStatus.ACTIVE,
            legs=legs,
            trigger_level=84000.0,
            trigger_deactivation_threshold=200.0,
            start_time=datetime(2025, 6, 1, 10, 0, 0),
            end_time=datetime(2025, 6, 30, 18, 45, 0),
            max_contracts_per_leg=10,
            sl_percent=30.0,
            tp_percent=150.0,
        )
        d = strat.to_dict()
        assert d["strategy_id"] == 5
        assert d["name"] == "test_strat"
        assert d["base_asset"] == "Si"
        assert d["status"] == "ACTIVE"
        assert len(d["legs"]) == 1
        assert d["legs"][0]["option_type"] == "CALL"
        assert d["trigger_level"] == 84000.0
        assert d["trigger_deactivation_threshold"] == 200.0
        assert d["max_contracts_per_leg"] == 10
        assert d["sl_percent"] == 30.0
        assert d["tp_percent"] == 150.0
        assert "created_at" in d
        assert "updated_at" in d

    def test_from_dict(self):
        """Десериализация StrategyDefinition из dict."""
        data = {
            "strategy_id": 3,
            "name": "Si iron condor",
            "base_asset": "Si",
            "status": "ACTIVE",
            "legs": [
                {"leg_index": 0, "option_type": "CALL", "strike": 90000.0, "sign": -1, "quantity": 1,
                 "iv_mode": "market", "manual_iv": None, "iv_multiplier": 1.0},
                {"leg_index": 1, "option_type": "PUT", "strike": 80000.0, "sign": -1, "quantity": 1,
                 "iv_mode": "market", "manual_iv": None, "iv_multiplier": 1.0},
            ],
            "trigger_level": 85000.0,
            "trigger_deactivation_threshold": 100.0,
            "start_time": "2025-06-01T10:00:00",
            "end_time": None,
            "max_contracts_per_leg": 5,
            "sl_percent": 50.0,
            "tp_percent": 100.0,
            "created_at": "2025-06-01T08:00:00",
            "updated_at": "2025-06-01T08:30:00",
        }
        strat = StrategyDefinition.from_dict(data)
        assert strat.strategy_id == 3
        assert strat.name == "Si iron condor"
        assert strat.base_asset == "Si"
        assert strat.status == StrategyStatus.ACTIVE
        assert len(strat.legs) == 2
        assert strat.legs[0].option_type == OptionType.CALL
        assert strat.legs[0].sign == -1
        assert strat.trigger_level == 85000.0
        assert strat.trigger_deactivation_threshold == 100.0
        assert strat.start_time == datetime(2025, 6, 1, 10, 0, 0)
        assert strat.end_time is None
        assert strat.sl_percent == 50.0

    def test_to_legs_json(self):
        """Сериализация ног в JSON строку."""
        legs = [
            Leg(leg_index=0, option_type=OptionType.CALL, strike=85000.0, sign=1, quantity=2),
            Leg(leg_index=1, option_type=OptionType.PUT, strike=82000.0, sign=-1, quantity=1),
        ]
        json_str = StrategyDefinition.to_legs_json(legs)
        parsed = json.loads(json_str)
        assert len(parsed) == 2
        assert parsed[0]["option_type"] == "CALL"
        assert parsed[1]["option_type"] == "PUT"
        assert parsed[1]["sign"] == -1

    def test_from_legs_json(self):
        """Десериализация ног из JSON строки."""
        json_str = json.dumps([
            {"leg_index": 0, "option_type": "CALL", "strike": 85000.0, "sign": 1, "quantity": 2,
             "iv_mode": "market", "manual_iv": None, "iv_multiplier": 1.0},
        ])
        legs = StrategyDefinition.from_legs_json(json_str)
        assert len(legs) == 1
        assert legs[0].option_type == OptionType.CALL
        assert legs[0].strike == 85000.0
        assert legs[0].sign == 1
        assert legs[0].quantity == 2

    def test_roundtrip_serialization(self):
        """Полный цикл: объект -> dict -> объект."""
        legs = [
            Leg(leg_index=0, option_type=OptionType.CALL, strike=85000.0, sign=1, quantity=1),
        ]
        original = StrategyDefinition(
            name="test",
            base_asset="Si",
            legs=legs,
            trigger_level=84000.0,
        )
        d = original.to_dict()
        restored = StrategyDefinition.from_dict(d)
        assert restored.name == original.name
        assert restored.base_asset == original.base_asset
        assert restored.trigger_level == original.trigger_level
        assert len(restored.legs) == len(original.legs)
        assert restored.legs[0].strike == original.legs[0].strike
        assert restored.legs[0].option_type == original.legs[0].option_type
        assert restored.legs[0].sign == original.legs[0].sign
        assert restored.status == original.status
