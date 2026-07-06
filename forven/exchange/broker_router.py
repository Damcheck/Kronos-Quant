"""Router for selecting the correct broker based on asset class."""

from typing import Any

from forven.symbol_mapping import AssetClass
from forven.exchange.broker_protocol import Broker


class BrokerRouter:
    """Routes orders and state queries to the appropriate broker."""
    
    def __init__(self):
        self._hl_broker = None
        self._mt5_broker = None
        
    def _get_hyperliquid(self) -> Broker:
        if self._hl_broker is None:
            from forven.exchange.hyperliquid import HyperliquidBroker
            self._hl_broker = HyperliquidBroker()
        return self._hl_broker
        
    def _get_mt5(self) -> Broker:
        if self._mt5_broker is None:
            from forven.exchange.mt5_broker import MT5Broker
            self._mt5_broker = MT5Broker()
        return self._mt5_broker
        
    def get_broker(self, asset_class: AssetClass) -> Broker:
        """Get the appropriate broker for the asset class."""
        if asset_class == AssetClass.CRYPTO:
            return self._get_hyperliquid()
        elif asset_class == AssetClass.FOREX:
            from forven.config import is_forex_enabled
            if not is_forex_enabled():
                raise ValueError(
                    "Forex/MT5 support is not enabled. Set FORVEN_ENABLE_FOREX=1 "
                    "or {\"enable_forex\": true} in config.json to opt in."
                )
            return self._get_mt5()
        elif asset_class == AssetClass.INDEX:
            from forven.config import is_forex_enabled
            if not is_forex_enabled():
                raise ValueError(
                    "Index trading via MT5 is not enabled. Set FORVEN_ENABLE_FOREX=1."
                )
            return self._get_mt5()
        else:
            raise ValueError(f"No broker configured for asset class {asset_class}")
            
    def get_all_brokers(self) -> list[Broker]:
        """Return all available brokers."""
        brokers = []
        try:
            brokers.append(self._get_hyperliquid())
        except Exception:
            pass
        try:
            brokers.append(self._get_mt5())
        except Exception:
            pass
        return brokers

# Global singleton router
router = BrokerRouter()

def get_broker_for_asset(asset_class: AssetClass) -> Broker:
    return router.get_broker(asset_class)

def get_all_brokers() -> list[Broker]:
    return router.get_all_brokers()
