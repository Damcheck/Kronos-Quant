"""Forex-specific risk and sizing logic.

Brief §4: This module is intentionally separate from risk.py's crypto sizing.
Do NOT merge forex and crypto sizing into a single function with if/else branches.
The dispatcher in broker_router.py selects the right module.
"""


def calculate_forex_position_size(
    account_equity: float,
    risk_pct: float,
    entry_price: float,
    stop_loss_price: float,
    pip_size: float | None = None,
    contract_size: float = 100_000.0,
    account_currency: str = "USD",
    quote_currency: str = "USD",
    conversion_rate: float = 1.0,
) -> float:
    """Lot-based position sizing for forex.

    Calculates the number of standard lots such that if the stop-loss is hit,
    the loss equals ``account_equity * risk_pct``.

    Args:
        account_equity: total account equity in the account currency.
        risk_pct: fraction of equity to risk on this trade (e.g. 0.01 = 1%).
        entry_price: intended entry price.
        stop_loss_price: protective stop-loss price.
        pip_size: size of one pip (0.0001 for majors, 0.01 for JPY pairs).
                  Auto-detected from the pair's quote digits if None.
        contract_size: units per standard lot (default 100,000).
        account_currency: the account's base currency (e.g. "USD").
        quote_currency: the pair's quote currency (right-hand side).
        conversion_rate: quote_currency → account_currency rate. 1.0 when the
                         quote currency IS the account currency (e.g. EURUSD
                         on a USD account).

    Returns:
        Position size in standard lots (may be fractional, e.g. 0.03 = 3 mini-lots).
    """
    if account_equity <= 0 or risk_pct <= 0:
        return 0.0
    if entry_price <= 0 or stop_loss_price <= 0:
        return 0.0

    # Auto-detect pip size from the pair if not provided
    if pip_size is None:
        # JPY pairs use 0.01, everything else uses 0.0001
        pip_size = 0.01 if quote_currency == "JPY" else 0.0001

    # Distance in price terms
    stop_distance = abs(entry_price - stop_loss_price)
    if stop_distance < 1e-10:
        return 0.0

    # Number of pips of risk
    pips_at_risk = stop_distance / pip_size

    # Dollar risk budget
    risk_budget = account_equity * risk_pct

    # Pip value per standard lot in the quote currency
    pip_value_per_lot_quote = contract_size * pip_size

    # Pip value per lot in account currency
    pip_value_per_lot_account = pip_value_per_lot_quote / conversion_rate

    # Lots = risk_budget / (pips_at_risk * pip_value_per_lot_account)
    if pip_value_per_lot_account <= 0 or pips_at_risk <= 0:
        return 0.0

    lots = risk_budget / (pips_at_risk * pip_value_per_lot_account)

    # Round to 2 decimal places (0.01 = 1 micro-lot, minimum on most brokers)
    lots = round(lots, 2)

    return max(lots, 0.0)


def get_forex_margin_requirement(
    asset: str,
    lot_size: float,
    account_leverage: float,
    current_price: float,
    contract_size: float = 100_000.0,
) -> float:
    """Calculate the margin required to open a forex position.

    margin = (lot_size × contract_size × current_price) / account_leverage

    If the base currency is the same as the account currency, current_price
    should be 1.0.
    """
    if account_leverage <= 0:
        raise ValueError("Account leverage must be positive.")
    return (lot_size * contract_size * current_price) / account_leverage


def calculate_pip_value(
    asset: str,
    lot_size: float,
    quote_currency: str,
    account_currency: str,
    conversion_rate: float = 1.0,
    pip_size: float | None = None,
) -> float:
    """Calculate the monetary value of a single pip for a given position size.

    For pairs where the quote currency matches the account currency (e.g.
    EURUSD on a USD account), conversion_rate is 1.0.
    For JPY pairs, pip_size is automatically set to 0.01.
    """
    if pip_size is None:
        pip_size = 0.01 if quote_currency == "JPY" else 0.0001

    position_size = lot_size * 100_000.0

    # pip value in quote currency
    pip_value_quote = position_size * pip_size

    # Convert to account currency
    return pip_value_quote / conversion_rate


def check_forex_correlation_limits(
    asset: str,
    direction: str,
    open_positions: list[dict],
    max_same_direction_usd: int = 3,
) -> tuple[bool, str]:
    """Check if opening this position exceeds correlation limits for forex.

    Uses a simple USD-exposure model: positions that are net-long USD vs.
    net-short USD are tracked separately, and a cap limits piling on.
    """
    # USD exposure mapping: going long EURUSD = shorting USD
    usd_short_pairs = ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"]
    usd_long_pairs = ["USDCAD", "USDCHF", "USDJPY"]

    # Determine this trade's net USD direction
    usd_exposure = 0
    if asset in usd_short_pairs:
        usd_exposure = -1 if direction.lower() == "long" else 1  # long EURUSD = short USD
    elif asset in usd_long_pairs:
        usd_exposure = 1 if direction.lower() == "long" else -1  # long USDJPY = long USD

    if usd_exposure == 0:
        return True, "No significant USD correlation group found."

    # Sum existing USD exposure from open positions
    current_usd = 0
    for pos in open_positions:
        pos_asset = pos.get("asset", "")
        pos_dir = pos.get("direction", "").lower()
        if pos_asset in usd_short_pairs:
            current_usd += (-1 if pos_dir == "long" else 1)
        elif pos_asset in usd_long_pairs:
            current_usd += (1 if pos_dir == "long" else -1)

    # Check if adding this trade exceeds the directional cap
    new_usd = current_usd + usd_exposure
    if abs(new_usd) > max_same_direction_usd:
        return False, (
            f"Max correlated USD exposure reached "
            f"(current={current_usd:+d}, adding={usd_exposure:+d}, "
            f"limit=±{max_same_direction_usd})."
        )

    return True, "Correlation limit OK."
