"""Cost models for Forex trading.

Brief §4.2: This is intentionally separate from the crypto cost model. Forex
has no single visible order book — spread must be modelled probabilistically
by session, and overnight swap/rollover must be charged per-direction.

DO NOT merge with costs_crypto.py.
"""

from __future__ import annotations

import math
from datetime import datetime


# ──────────────────────────────────────────────────────────────────────
# Session-aware variable spread (brief §4.2)
# ──────────────────────────────────────────────────────────────────────

# Typical raw spreads in pips by session.  Source: broker tick data averages.
# These should be calibrated against real MT5 broker data before trusting
# gauntlet verdicts on forex strategies (brief §4.2, paragraph 3).
_SESSION_SPREADS: dict[str, dict[str, float]] = {
    # Pair → {session → spread in pips}
    "EURUSD": {"london_ny":  0.1, "london": 0.2, "ny": 0.3, "asia": 0.5, "off": 1.5},
    "GBPUSD": {"london_ny":  0.3, "london": 0.4, "ny": 0.5, "asia": 0.9, "off": 2.0},
    "USDJPY": {"london_ny":  0.2, "london": 0.3, "ny": 0.3, "asia": 0.4, "off": 1.2},
    "AUDUSD": {"london_ny":  0.3, "london": 0.4, "ny": 0.5, "asia": 0.3, "off": 1.5},
    "USDCAD": {"london_ny":  0.4, "london": 0.5, "ny": 0.5, "asia": 0.8, "off": 2.0},
    "USDCHF": {"london_ny":  0.3, "london": 0.4, "ny": 0.5, "asia": 0.7, "off": 1.8},
    "NZDUSD": {"london_ny":  0.5, "london": 0.6, "ny": 0.7, "asia": 0.5, "off": 2.0},
}

# Fallback spread for pairs not in the table above
_DEFAULT_SPREAD: dict[str, float] = {
    "london_ny": 1.0, "london": 1.2, "ny": 1.5, "asia": 2.0, "off": 3.0,
}


def _classify_session(utc_hour: int) -> str:
    """Classify the current UTC hour into a forex session bucket.

    Session definitions (approximate, UTC):
      London:   07–16
      New York: 12–21
      Overlap:  12–16  (london_ny)
      Asia:     00–07
      Off:      21–00 (thin liquidity)
    """
    if 12 <= utc_hour < 16:
        return "london_ny"
    elif 7 <= utc_hour < 12:
        return "london"
    elif 16 <= utc_hour < 21:
        return "ny"
    elif 0 <= utc_hour < 7:
        return "asia"
    else:
        return "off"


def get_spread_pips(asset: str, utc_hour: int | None = None) -> float:
    """Return the expected spread in pips for *asset* at the given UTC hour.

    If ``utc_hour`` is None, uses the current system time.
    """
    if utc_hour is None:
        utc_hour = datetime.utcnow().hour
    session = _classify_session(utc_hour)
    pair_spreads = _SESSION_SPREADS.get(asset, _DEFAULT_SPREAD)
    return pair_spreads.get(session, pair_spreads.get("off", 2.0))


# ──────────────────────────────────────────────────────────────────────
# Overnight swap/rollover (brief §4.2)
# ──────────────────────────────────────────────────────────────────────

# Swap rates in points per standard lot per night (direction-dependent).
# Negative = cost, positive = credit.  These MUST be refreshed from the
# broker's symbol specification regularly (mt5.symbol_info().swap_long / swap_short).
# Placeholder values below are illustrative — replace with actuals.
_SWAP_RATES: dict[str, dict[str, float]] = {
    # pair → {"long": points/night, "short": points/night}
    "EURUSD": {"long": -6.3,  "short":  1.2},
    "GBPUSD": {"long": -4.8,  "short": -0.5},
    "USDJPY": {"long":  3.1,  "short": -8.2},
    "AUDUSD": {"long": -3.5,  "short":  0.2},
    "USDCAD": {"long": -1.0,  "short": -4.0},
    "USDCHF": {"long":  2.5,  "short": -7.0},
    "NZDUSD": {"long": -2.0,  "short": -1.5},
}


def get_overnight_swap_cost(
    asset: str,
    direction: str,
    lot_size: float,
    holding_nights: int = 1,
    pip_size: float | None = None,
) -> float:
    """Calculate the swap/rollover cost for holding a position overnight.

    Returns a SIGNED value: negative = cost to the trader, positive = credit.
    Wednesday night counts as triple (to cover Saturday/Sunday).

    Args:
        asset: pair symbol, e.g. "EURUSD".
        direction: "long" or "short".
        lot_size: position size in standard lots.
        holding_nights: number of overnight rollovers. Pass 3 for a Wednesday hold
                        or use the ``triple_wednesday`` flag in the caller.
        pip_size: point size (defaults to 0.0001, 0.01 for JPY pairs).
    """
    if pip_size is None:
        pip_size = 0.01 if "JPY" in asset else 0.0001

    swap_table = _SWAP_RATES.get(asset)
    if swap_table is None:
        return 0.0  # unknown pair — conservative: charge nothing (caller should warn)

    swap_points = swap_table.get(direction.lower(), 0.0)

    # points × pip_size × contract_size × lots × nights
    contract_size = 100_000.0
    cost = swap_points * pip_size * contract_size * lot_size * holding_nights
    return cost


# ──────────────────────────────────────────────────────────────────────
# Combined cost function
# ──────────────────────────────────────────────────────────────────────

def get_forex_trading_cost(
    asset: str,
    price: float,
    lot_size: float,
    direction: str = "long",
    utc_hour: int | None = None,
    holding_nights: int = 0,
    commission_per_lot: float = 3.50,
) -> float:
    """Total estimated cost for a forex round-trip (entry + exit spread + commission + swap).

    Args:
        asset: pair symbol.
        price: reference price for notional calculation.
        lot_size: position size in standard lots.
        direction: "long" or "short" — affects swap sign.
        utc_hour: UTC hour of entry (for session-dependent spread). None = now.
        holding_nights: overnight rollovers expected (0 for intraday).
        commission_per_lot: round-turn commission per standard lot in USD.

    Returns:
        Total estimated cost in quote currency (positive = cost).
    """
    contract_size = 100_000.0
    position_size = lot_size * contract_size

    pip_size = 0.01 if "JPY" in asset else 0.0001

    # 1. Spread cost (entry + exit → 2 × half-spread crossings)
    spread_pips = get_spread_pips(asset, utc_hour)
    spread_cost = position_size * (spread_pips * pip_size)

    # 2. Commission
    commission_cost = lot_size * commission_per_lot

    # 3. Swap/rollover (only if holding overnight)
    swap_cost = 0.0
    if holding_nights > 0:
        swap_cost = get_overnight_swap_cost(
            asset, direction, lot_size, holding_nights, pip_size
        )
        # swap_cost is signed (negative = cost), we want unsigned total cost
        swap_cost = abs(swap_cost) if swap_cost < 0 else -swap_cost  # credit reduces cost

    return spread_cost + commission_cost + swap_cost
