import websocket
import json
import pandas as pd
import numpy as np
from binance.client import Client
from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET
import time
from datetime import datetime
import os

# Initialize Binance client
api_key = os.getenv('api_key')
api_secret = os.getenv('api_secret')
client = Client(api_key, api_secret)

# Trading parameters
symbol = 'LTCUSDT'
timeframe = '5m'
short_window = 7
long_window = 30
leverage = 10

# Store price data and position state
prices = []
last_signal = None  # 'long', 'short', or None

def setup_leverage():
    try:
        # Set leverage
        client.futures_change_leverage(symbol=symbol, leverage=leverage)
        print(f"Leverage set to {leverage}x")
        
        # Set margin type to isolated
        client.futures_change_margin_type(symbol=symbol, marginType='ISOLATED')
        print("Margin type set to ISOLATED")
    except Exception as e:
        print(f"Error setting up leverage: {e}")

def calculate_position_size():
    try:
        # Get account balance
        account = client.futures_account_balance()
        usdt_balance = next((float(balance['balance']) for balance in account if balance['asset'] == 'USDT'), 0)
        
        # Get current price
        ticker = client.futures_symbol_ticker(symbol=symbol)
        current_price = float(ticker['price'])
        
        # Calculate position size (using 95% of balance for safety)
        position_size = (usdt_balance * 0.95) / current_price
        return round(position_size, 3)  # Round to 3 decimal places
    except Exception as e:
        print(f"Error calculating position size: {e}")
        return 0

def on_message(ws, message):
    global prices, last_signal
    
    # Parse the websocket message
    data = json.loads(message)
    price = float(data['k']['c'])  # Closing price
    
    # Add price to our list
    prices.append(price)
    
    # Keep only the last long_window prices
    if len(prices) > long_window:
        prices = prices[-long_window:]
    
    # Calculate moving averages
    if len(prices) >= long_window:
        short_ma = np.mean(prices[-short_window:])
        long_ma = np.mean(prices)
        
        # Get current position
        position = get_position()
        
        # Calculate new position size
        quantity = calculate_position_size()
        
        # Determine current signal
        current_signal = 'long' if short_ma > long_ma else 'short'
        
        # Only trade on signal change (crossover)
        if current_signal != last_signal:
            if current_signal == 'long':          # BUY signal
                if position < 0:  # If we have a short position
                    place_order(SIDE_BUY, abs(position))    #close original order
                    place_order(SIDE_BUY, quantity)
                elif position == 0:  
                    place_order(SIDE_BUY, quantity)
                    
            elif current_signal == 'short':        # Sell signal
                if position > 0:  # If we have a long position
                    place_order(SIDE_SELL, abs(position))       #close original order
                    place_order(SIDE_SELL, quantity)
                elif position == 0:  
                    place_order(SIDE_SELL, quantity)
            
            # Update last signal
            last_signal = current_signal
            print(f"Signal changed to: {current_signal}")

def on_error(ws, error):
    print(f"Error: {error}")

def on_close(ws, close_status_code, close_msg):
    print("WebSocket connection closed")

def on_open(ws):
    print("WebSocket connection opened")
    
    # Subscribe to klines stream
    subscribe_message = {
        "method": "SUBSCRIBE",
        "params": [f"{symbol.lower()}@kline_{timeframe}"],
        "id": 1
    }
    ws.send(json.dumps(subscribe_message))

def get_position():
    try:
        # Get current position from futures account
        position_info = client.futures_position_information(symbol=symbol)
        if position_info:
            return float(position_info[0]['positionAmt'])
        return 0
    except Exception as e:
        print(f"Error getting position: {e}")
        return 0

def place_order(side, quantity):
    try:
        order = client.futures_create_order(
            symbol=symbol,
            side=side,
            type=ORDER_TYPE_MARKET,
            quantity=quantity
        )
        print(f"Order placed: {order}")
    except Exception as e:
        print(f"Error placing order: {e}")

def close_all_positions():
    try:
        position = get_position()
        if position > 0:  # Long position
            place_order(SIDE_SELL, abs(position))
            print("Closed long position")
        elif position < 0:  # Short position
            place_order(SIDE_BUY, abs(position))
            print("Closed short position")
        else:
            print("No positions to close")
    except Exception as e:
        print(f"Error closing positions: {e}")

def main():
    try:
        # Setup leverage and margin type
        setup_leverage()
        
        # Initialize websocket connection
        websocket.enableTrace(True)
        ws = websocket.WebSocketApp(
            "wss://fstream.binance.com/ws",  # Futures websocket endpoint
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open
        )
        
        # Run websocket
        ws.run_forever()
        
    except KeyboardInterrupt:
        print("\nProgram terminated by user")
        close_all_positions()
        print("All positions closed. Program ended.")
    except Exception as e:
        print(f"Unexpected error: {e}")
        close_all_positions()
        print("All positions closed. Program ended.")

if __name__ == "__main__":
    main()
