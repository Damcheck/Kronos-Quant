"""Cost models for Crypto trading.

Brief §4.2: This extracts the crypto-specific cost assumptions so they are
structurally separate from the new forex model (costs_forex.py).

For crypto, costs consist of:
1. Exchange maker/taker fees (static bps per trade).
2. Slippage (estimated bps impact).
3. Funding rate (Hyperliquid perp funding, applied bar-by-bar by the backtester).
"""

from __future__ import annotations


def get_crypto_stress_fees(
    baseline_fee_bps: float,
    baseline_slippage_bps: float,
    fee_multiplier: float,
    slippage_multiplier: float,
) -> tuple[float, float]:
    """Calculate stressed fee and slippage for the Gauntlet cost-stress suite.
    
    Unlike Forex where spread and swap vary probabilistically by session and direction,
    crypto fees are generally static percentage charges. This scales the baseline
    assumptions by the stress multipliers.
    
    The backtester independently applies real historical funding rates bar-by-bar.
    """
    return (
        baseline_fee_bps * float(fee_multiplier),
        baseline_slippage_bps * float(slippage_multiplier),
    )
