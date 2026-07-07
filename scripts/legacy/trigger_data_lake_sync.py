import httpx
import sys

URL = "http://127.0.0.1:9050/data/ingest"

SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
    "ADA/USDT", "DOGE/USDT", "TRX/USDT", "AVAX/USDT", "LINK/USDT"
]

TIMEFRAMES = [
    "1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"
]

def trigger_bg_ingests(url=URL):
    for symbol in SYMBOLS:
        for tf in TIMEFRAMES:
            print(f"Triggering {symbol} {tf}...")
            payload = {
                "symbol": symbol,
                "timeframe": tf,
                "exchange": "binance",
                "all_available": True
            }
            try:
                # We request directly to the remote engine! Note: You can change the URL if needed.
                resp = httpx.post(url, json=payload, timeout=30.0)
                resp.raise_for_status()
                data = resp.json()
                print(f" -> Queued successfully [Run ID: {data.get('run_id')}]")
            except Exception as e:
                print(f" -> Failed: {e}")

if __name__ == "__main__":
    url_arg = URL
    if len(sys.argv) > 1:
        url_arg = sys.argv[1]
    trigger_bg_ingests(url_arg)
