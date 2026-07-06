import pytest

from forven.exchange.risk_forex import calculate_forex_position_size
from forven.gauntlet.costs_forex import _classify_session, calculate_forex_spread_cost
from forven.exchange.risk import get_risk_partition

def test_calculate_forex_position_size():
    # 10k USD account, 1% risk = $100 risk.
    # EURUSD entry 1.1000, stop 1.0900 -> 100 pips risk.
    # Pip value for 1 standard lot (100k) is $10.
    # 100 pips * $10/pip = $1000 risk per standard lot.
    # So we should trade 0.1 lots to risk $100.
    
    lots = calculate_forex_position_size(
        account_equity=10000.0,
        risk_pct=0.01,
        entry_price=1.1000,
        stop_loss_price=1.0900,
        pip_size=0.0001,
        contract_size=100000.0,
        account_currency="USD",
        quote_currency="USD",
        conversion_rate=1.0,
    )
    
    assert lots == pytest.approx(0.1, 0.01)

def test_forex_cost_session():
    # 14 UTC is London/NY overlap
    assert _classify_session(14) == "london_ny"
    # 22 UTC is Off hours
    assert _classify_session(22) == "off"
    
def test_calculate_forex_spread_cost():
    # EURUSD in london_ny should be 0.1 pips according to the table
    # 1 pip = 0.0001. So spread cost = 0.00001 per unit.
    # For 1 standard lot (100k), 100,000 * 0.00001 = 1 USD cost per lot per side?
    # No, it just calculates the absolute dollar value.
    cost = calculate_forex_spread_cost(
        symbol="EURUSD",
        notional_base=100000.0,
        utc_hour=14,
        pip_size=0.0001,
    )
    # 0.1 pips * 100,000 * 0.0001 = 1.0
    assert cost == pytest.approx(1.0, 0.01)

def test_risk_partition_defaults():
    # Test that get_risk_partition works
    partition = get_risk_partition({})
    assert partition["crypto_budget_fraction"] == 0.70
    assert partition["forex_budget_fraction"] == 0.30
    assert partition["crypto_max_concurrent"] == 10
    assert partition["forex_max_concurrent"] == 5
    assert partition["kill_switch_per_asset_class"] is True
