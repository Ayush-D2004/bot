import websocket
import json
import numpy as np
import os
import requests
# Trading parameters
symbol = 'LTCUSDT'
timeframe = '5m'
short_window = 7
long_window = 30

# Telegram settings
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
 
# Store price data and position state
prices = []
last_signal = None  # 'long', 'short', or None

def send_telegram_message(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message
        }
        requests.post(url, data=payload)
    except Exception as e:
        print(f"Failed to send Telegram message: {e}")

def on_message(ws, message):
    global prices, last_signal

    data = json.loads(message)

    if 'k' in data and data['k']['x']:  # Only process closed candles
        close_price = float(data['k']['c'])
        prices.append(close_price)

        if len(prices) > long_window:
            prices = prices[-long_window:]

        if len(prices) >= long_window:
            short_ma = np.mean(prices[-short_window:])
            long_ma = np.mean(prices)

            current_signal = 'long' if short_ma > long_ma else 'short'

            if current_signal != last_signal:
                message = f"🔔 Signal changed to: {current_signal.upper()} ({symbol})\nShort MA: {short_ma:.4f}\nLong MA: {long_ma:.4f}"
                print(message)
                send_telegram_message(message)
                last_signal = current_signal

def on_error(ws, error):
    print(f"WebSocket error: {error}")

def on_close(ws, close_status_code, close_msg):
    print("WebSocket closed")
    send_telegram_message(f"⚠️ Trading bot disconnected from WebSocket for {symbol}.")


def on_open(ws):
    print("WebSocket connected")
    send_telegram_message(f"✅ Trading bot connected to WebSocket for {symbol} on {timeframe} timeframe.")
    subscribe_message = {
        "method": "SUBSCRIBE",
        "params": [f"{symbol.lower()}@kline_{timeframe}"],
        "id": 1
    }
    ws.send(json.dumps(subscribe_message))

def main():
    websocket.enableTrace(False)
    ws = websocket.WebSocketApp(
        "wss://fstream.binance.com/ws",
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
        on_open=on_open
    )

    try:
        ws.run_forever()
    except KeyboardInterrupt:
        print("Program interrupted")
    except Exception as e:
        print(f"Unexpected error: {e}")

if __name__ == "__main__":
    main()
