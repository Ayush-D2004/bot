import websocket
import json
import numpy as np
import os
import requests
from flask import Flask
from threading import Thread
import time

# === Trading parameters ===
symbol = 'LTCUSDT'
timeframe = '5m'
short_window = 7
long_window = 30

# === Telegram settings ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# === State ===
prices = []
last_signal = None

def send_telegram_message(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message
        }
        requests.post(url, data=payload)
    except Exception as e:
        print(f"Telegram error: {e}")

def on_message(ws, message):
    global prices, last_signal
    data = json.loads(message)

    if 'k' in data and data['k']['x']:
        close_price = float(data['k']['c'])
        prices.append(close_price)

        if len(prices) > long_window:
            prices = prices[-long_window:]

        if len(prices) >= long_window:
            short_ma = np.mean(prices[-short_window:])
            long_ma = np.mean(prices)

            signal = 'long' if short_ma > long_ma else 'short'
            if signal != last_signal:
                msg = (
                    f"🔔 Signal changed to: {signal.upper()} ({symbol})\n"
                    f"Short MA: {short_ma:.4f}, Long MA: {long_ma:.4f}"
                )
                print(msg)
                send_telegram_message(msg)
                last_signal = signal

def on_error(ws, error):
    print("WebSocket error:", error)

def on_close(ws, code, msg):
    print("WebSocket closed")
    send_telegram_message("⚠️ Bot disconnected from WebSocket.")

def on_open(ws):
    print("WebSocket connected")
    send_telegram_message(f"✅ Bot started for {symbol} on {timeframe}")
    subscribe_message = {
        "method": "SUBSCRIBE",
        "params": [f"{symbol.lower()}@kline_{timeframe}"],
        "id": 1
    }
    ws.send(json.dumps(subscribe_message))

def run_bot():
    while True:
        try:
            ws = websocket.WebSocketApp(
                "wss://fstream.binance.com/ws",
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
                on_open=on_open
            )
            ws.run_forever()
        except Exception as e:
            print("Bot crashed, restarting:", e)
            send_telegram_message(f"⚠️ Bot crashed: {e}")
        time.sleep(5)

# === Flask app ===
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Telegram Trading Bot is running!"

def start_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))

# === Main entry ===
if __name__ == "__main__":
    # Run Flask and bot in parallel
    Thread(target=start_flask).start()
    Thread(target=run_bot).start()
