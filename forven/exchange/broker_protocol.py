"""
Abstract interface for Forven exchange/broker adapters.
"""

from typing import Any, Protocol

class Broker(Protocol):
    """
    Common interface for executing trades and querying state across asset classes.
    """
    
    def get_account_value(self, testnet: bool = True, **kwargs) -> dict[str, Any]:
        """
        Fetch the current account value and margin usage.
        Returns a dictionary with at least 'accountValue' and 'totalMarginUsed' keys.
        """
        ...
        
    def place_order(
        self,
        symbol: str,
        is_buy: bool,
        size: float,
        price: float | None = None,
        testnet: bool = True,
        **kwargs
    ) -> Any:
        """
        Place an order on the exchange/broker.
        Returns the order response or identifier.
        """
        ...
        
    def cancel_order(
        self,
        symbol: str,
        order_id: str,
        testnet: bool = True,
        **kwargs
    ) -> Any:
        """
        Cancel a specific order.
        """
        ...
        
    def get_positions(self, testnet: bool = True, **kwargs) -> list[dict[str, Any]]:
        """
        Fetch all open positions on the exchange.
        """
        ...
        
    def close_position(
        self,
        symbol: str,
        testnet: bool = True,
        **kwargs
    ) -> Any:
        """
        Market close an open position.
        """
        ...
        
    def get_current_price(
        self,
        symbol: str,
        testnet: bool = True,
        **kwargs
    ) -> float:
        """
        Get the latest mid or execution price for the symbol.
        """
        ...
