"""MetaTrader 5 broker adapter for Forex execution."""

import logging
import threading
from typing import Any

from forven.exchange.broker_protocol import Broker

log = logging.getLogger("forven.exchange.mt5")

_MT5_LOCK = threading.Lock()
_MT5_INITIALIZED = False


def _get_mt5():
    """Lazy load MetaTrader5 to avoid hard dependency on non-Windows platforms."""
    try:
        import MetaTrader5 as mt5
        return mt5
    except ImportError:
        raise RuntimeError(
            "MetaTrader5 package is not installed or not supported on this OS. "
            "Install with `pip install forven[forex]` on Windows."
        )


def _init_mt5() -> bool:
    """Initialize MT5 and login using credentials from auth.json."""
    global _MT5_INITIALIZED
    
    if _MT5_INITIALIZED:
        return True
        
    mt5 = _get_mt5()
    
    with _MT5_LOCK:
        if _MT5_INITIALIZED:
            return True
            
        if not mt5.initialize():
            log.error("MT5 initialize() failed, error code: %s", mt5.last_error())
            return False
            
        # Get credentials via the proper secret_storage accessor (brief §3.7)
        from forven.secret_storage import get_mt5_credentials
        
        creds = get_mt5_credentials()
        if creds is None:
            log.warning("No MT5 credentials configured. Use secret_storage.set_mt5_credentials().")
            return False
            
        try:
            authorized = mt5.login(
                int(creds["login"]),
                password=creds["password"],
                server=creds["server"],
            )
            if not authorized:
                log.error("MT5 login failed, error code: %s", mt5.last_error())
                return False
                
            _MT5_INITIALIZED = True
            log.info("MT5 initialized and logged in successfully.")
            return True
            
        except Exception as e:
            log.error("Error during MT5 login: %s", e)
            return False


class MT5Broker:
    """MT5 implementation of the Broker protocol."""
    
    def get_account_value(self, testnet: bool = True, **kwargs) -> dict[str, Any]:
        if not _init_mt5():
            raise RuntimeError("MT5 not initialized")
            
        mt5 = _get_mt5()
        with _MT5_LOCK:
            account_info = mt5.account_info()
            if account_info is None:
                raise RuntimeError(f"Failed to get MT5 account info: {mt5.last_error()}")
                
            # Map MT5 fields to expected generic fields
            return {
                "accountValue": account_info.equity,
                "totalMarginUsed": account_info.margin,
                "freeMargin": account_info.margin_free,
                "currency": account_info.currency,
            }

    def place_order(
        self,
        symbol: str,
        is_buy: bool,
        size: float,
        price: float | None = None,
        testnet: bool = True,
        **kwargs
    ) -> Any:
        if not _init_mt5():
            raise RuntimeError("MT5 not initialized")
            
        mt5 = _get_mt5()
        
        with _MT5_LOCK:
            # Ensure symbol is in market watch
            if not mt5.symbol_select(symbol, True):
                raise RuntimeError(f"Failed to select symbol {symbol} in MT5")
                
            symbol_info = mt5.symbol_info(symbol)
            if symbol_info is None:
                raise RuntimeError(f"Symbol {symbol} not found in MT5")
                
            order_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL
            
            # Simple market execution for now unless limit price logic is needed
            if price is None:
                price = mt5.symbol_info_tick(symbol).ask if is_buy else mt5.symbol_info_tick(symbol).bid
                action = mt5.TRADE_ACTION_DEAL
            else:
                action = mt5.TRADE_ACTION_PENDING
                order_type = mt5.ORDER_TYPE_BUY_LIMIT if is_buy else mt5.ORDER_TYPE_SELL_LIMIT
                
            request = {
                "action": action,
                "symbol": symbol,
                "volume": float(size),
                "type": order_type,
                "price": float(price),
                "deviation": 20,
                "magic": kwargs.get("magic", 100),
                "comment": kwargs.get("comment", "forven"),
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            
            result = mt5.order_send(request)
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                raise RuntimeError(f"MT5 order_send failed: {result.comment} (code {result.retcode})")
                
            return result

    def cancel_order(
        self,
        symbol: str,
        order_id: str,
        testnet: bool = True,
        **kwargs
    ) -> Any:
        if not _init_mt5():
            raise RuntimeError("MT5 not initialized")
            
        mt5 = _get_mt5()
        
        with _MT5_LOCK:
            request = {
                "action": mt5.TRADE_ACTION_REMOVE,
                "order": int(order_id),
            }
            result = mt5.order_send(request)
            if result.retcode != mt5.TRADE_RETCODE_DONE:
                raise RuntimeError(f"MT5 order cancel failed: {result.comment} (code {result.retcode})")
            return result

    def get_positions(self, testnet: bool = True, **kwargs) -> list[dict[str, Any]]:
        if not _init_mt5():
            raise RuntimeError("MT5 not initialized")
            
        mt5 = _get_mt5()
        
        with _MT5_LOCK:
            positions = mt5.positions_get()
            if positions is None:
                # MT5 returns None on error OR if no positions. Check last_error
                error = mt5.last_error()
                if error[0] == 1: # SUCCESS
                    return []
                raise RuntimeError(f"Failed to get MT5 positions: {error}")
                
            # Normalize positions
            norm_positions = []
            for p in positions:
                norm_positions.append({
                    "symbol": p.symbol,
                    "size": p.volume if p.type == mt5.ORDER_TYPE_BUY else -p.volume,
                    "entry_price": p.price_open,
                    "unrealized_pnl": p.profit,
                    "ticket": p.ticket,
                    "magic": p.magic
                })
            return norm_positions

    def close_position(
        self,
        symbol: str,
        testnet: bool = True,
        **kwargs
    ) -> Any:
        if not _init_mt5():
            raise RuntimeError("MT5 not initialized")
            
        mt5 = _get_mt5()
        
        with _MT5_LOCK:
            positions = mt5.positions_get(symbol=symbol)
            if not positions:
                return None
                
            results = []
            for p in positions:
                is_buy = p.type == mt5.ORDER_TYPE_BUY
                close_type = mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY
                price = mt5.symbol_info_tick(symbol).bid if is_buy else mt5.symbol_info_tick(symbol).ask
                
                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": symbol,
                    "volume": p.volume,
                    "type": close_type,
                    "position": p.ticket,
                    "price": price,
                    "deviation": 20,
                    "magic": p.magic,
                    "comment": "forven_close",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": mt5.ORDER_FILLING_IOC,
                }
                result = mt5.order_send(request)
                if result.retcode != mt5.TRADE_RETCODE_DONE:
                    raise RuntimeError(f"MT5 close position failed: {result.comment} (code {result.retcode})")
                results.append(result)
            return results

    def get_current_price(
        self,
        symbol: str,
        testnet: bool = True,
        **kwargs
    ) -> float:
        if not _init_mt5():
            raise RuntimeError("MT5 not initialized")
            
        mt5 = _get_mt5()
        
        with _MT5_LOCK:
            if not mt5.symbol_select(symbol, True):
                raise RuntimeError(f"Failed to select symbol {symbol} in MT5")
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                raise RuntimeError(f"Failed to get tick for {symbol}")
            # return mid price
            return (tick.bid + tick.ask) / 2.0
