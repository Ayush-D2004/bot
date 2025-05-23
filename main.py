import websocket
import json
import pandas as pd
import numpy as np
from binance.client import Client
from binance.exceptions import APIError
from binance.enums import SIDE_BUY, SIDE_SELL, ORDER_TYPE_MARKET, FUTURE_ORDER_TYPE_STOP_MARKET
import time
from datetime import datetime
import os
import logging
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import asyncio
from typing import Optional
import threading
from flask import Flask
from decimal import Decimal, ROUND_DOWN

# Initialize Flask app
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "Bot is running!"

@flask_app.route('/health')
def health_check():
    return "OK", 200

def run_flask():
    flask_app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('trading_bot.log'),
        logging.StreamHandler()
    ]
)

# Initialize Binance client
api_key = os.getenv('api_key')
api_secret = os.getenv('api_secret')
client = Client(api_key, api_secret)
client.FUTURES_URL = 'https://fapi.binance.com'

# Trading parameters
symbol = 'LTCUSDT'
timeframe = '5m'
short_window = 7
long_window = 30
leverage = 10
ACCOUNT_USAGE_PERCENTAGE = 95  # Use 95% of account balance
STOP_LOSS_PERCENTAGE = 2  # 2% stop loss

# Store price data and position state
prices = []
last_signal = None  # 'long', 'short', or None
last_websocket_message = time.time()
WEBSOCKET_TIMEOUT = 60  # seconds

# Telegram configuration
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# Store the application globally
telegram_app: Optional[Application] = None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a message when the command /start is issued."""
    if update.message is None:
        return
        
    keyboard = [
        [InlineKeyboardButton("Check Position", callback_data='check_position')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('Welcome to the Trading Bot! Use the button below to check your position:', reply_markup=reply_markup)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle button presses."""
    if update.callback_query is None:
        return
        
    query = update.callback_query
    await query.answer()
    
    if query.data == 'check_position':
        try:
            # Get account balance
            account = client.futures_account_balance()
            usdt_balance = next((float(balance['balance']) for balance in account if balance['asset'] == 'USDT'), 0)
            
            # Get current position
            position = get_position()
            position_info = client.futures_position_information(symbol=symbol)
            position_data = position_info[0] if position_info else None
            
            # Get current price
            ticker = client.futures_symbol_ticker(symbol=symbol)
            current_price = float(ticker['price'])
            
            # Calculate unrealized PNL
            unrealized_pnl = float(position_data['unRealizedProfit']) if position_data else 0
            
            # Format message
            message = f"💰 <b>Account Status</b>\n\n"
            message += f"Balance: {usdt_balance:.2f} USDT\n"
            message += f"Current Position: {abs(position):.3f} {symbol}\n"
            message += f"Position Type: {'Long' if position > 0 else 'Short' if position < 0 else 'None'}\n"
            message += f"Entry Price: {position_data['entryPrice'] if position_data else 'N/A'}\n"
            message += f"Current Price: {current_price}\n"
            message += f"Unrealized PNL: {unrealized_pnl:.2f} USDT\n"
            message += f"Leverage: {leverage}x\n"
            message += f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            
            # Add refresh button
            keyboard = [[InlineKeyboardButton("Refresh", callback_data='check_position')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(text=message, reply_markup=reply_markup, parse_mode='HTML')
            
        except Exception as e:
            error_message = f"❌ <b>Error Getting Position</b>\n{str(e)}"
            await query.edit_message_text(text=error_message, parse_mode='HTML')

async def check_position_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /status command"""
    if update.message is None:
        return
        
    try:
        # Get account balance
        account = client.futures_account_balance()
        if not account:
            await update.message.reply_text("❌ Failed to get account balance")
            return
            
        usdt_balance = next((float(balance['balance']) for balance in account if balance['asset'] == 'USDT'), 0)
        if usdt_balance <= 0:
            await update.message.reply_text("❌ Invalid USDT balance")
            return
        
        # Get current position
        position = get_position()
        position_info = client.futures_position_information(symbol=symbol)
        position_data = position_info[0] if position_info else None
        
        # Get current price
        ticker = client.futures_symbol_ticker(symbol=symbol)
        current_price = float(ticker['price'])
        
        # Calculate unrealized PNL
        unrealized_pnl = float(position_data['unRealizedProfit']) if position_data else 0
        
        # Format message
        message = f"💰 <b>Account Status</b>\n\n"
        message += f"Balance: {usdt_balance:.2f} USDT\n"
        message += f"Current Position: {abs(position):.3f} {symbol}\n"
        message += f"Position Type: {'Long' if position > 0 else 'Short' if position < 0 else 'None'}\n"
        message += f"Entry Price: {position_data['entryPrice'] if position_data else 'N/A'}\n"
        message += f"Current Price: {current_price}\n"
        message += f"Unrealized PNL: {unrealized_pnl:.2f} USDT\n"
        message += f"Leverage: {leverage}x\n"
        message += f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        await update.message.reply_text(message, parse_mode='HTML')
        
    except Exception as e:
        error_message = f"❌ <b>Error Getting Status</b>\n{str(e)}"
        await update.message.reply_text(error_message, parse_mode='HTML')

def send_telegram_message(message: str) -> None:
    """Send a message to Telegram"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }
        response = requests.post(url, data=data)
        if response.status_code != 200:
            logging.error(f"Failed to send Telegram message: {response.text}")
    except Exception as e:
        logging.error(f"Error sending Telegram message: {e}")

def setup_leverage():
    try:
        # Set leverage
        client.futures_change_leverage(symbol=symbol, leverage=leverage)
        logging.info(f"Leverage set to {leverage}x")
        
        # Set margin type to isolated
        client.futures_change_margin_type(symbol=symbol, marginType='ISOLATED')
        logging.info("Margin type set to ISOLATED")
    except Exception as e:
        logging.error(f"Error setting up leverage: {e}")

def calculate_position_size():
    try:
        # Get account balance
        account = client.futures_account_balance()
        if not account:
            logging.error("Failed to get account balance")
            return 0
            
        usdt_balance = next((float(balance['balance']) for balance in account if balance['asset'] == 'USDT'), 0)
        if usdt_balance <= 0:
            logging.error(f"Invalid USDT balance: {usdt_balance}")
            return 0
        
        # Get current price
        ticker = client.futures_symbol_ticker(symbol=symbol)
        if not ticker or 'price' not in ticker:
            logging.error("Failed to get current price")
            return 0
            
        current_price = float(ticker['price'])
        if current_price <= 0:
            logging.error(f"Invalid current price: {current_price}")
            return 0
        
        # Calculate position size (using 95% of balance with leverage)
        position_size = (usdt_balance * (ACCOUNT_USAGE_PERCENTAGE / 100) * leverage) / current_price
        
        # Log the calculation details
        logging.info(f"Position size calculation: Balance={usdt_balance}, Price={current_price}, Size={position_size}")
        
        return round(position_size, 3)  # Round to 3 decimal places
    except Exception as e:
        logging.error(f"Error calculating position size: {str(e)}")
        logging.error(f"Error type: {type(e).__name__}")
        if isinstance(e, Exception) and hasattr(e, '__dict__'):
            logging.error(f"Error details: {e.__dict__}")
        return 0

def place_stop_loss(entry_price, side):
    try:
        # If entry_price is None or 0, fetch current price
        if not entry_price:
            ticker = client.futures_symbol_ticker(symbol=symbol)
            entry_price = float(ticker['price'])
            
        stop_price = entry_price * (1 - STOP_LOSS_PERCENTAGE/100) if side == SIDE_BUY else entry_price * (1 + STOP_LOSS_PERCENTAGE/100)
        position = get_position()
        
        if position != 0:
            order = client.futures_create_order(
                symbol=symbol,
                side=SIDE_SELL if side == SIDE_BUY else SIDE_BUY,
                type=FUTURE_ORDER_TYPE_STOP_MARKET,
                stopPrice=stop_price,
                quantity=abs(position),
                reduceOnly=True
            )
            message = f"⚠️ <b>Stop Loss Placed</b>\n"
            message += f"Symbol: {symbol}\n"
            message += f"Type: {'Long' if side == SIDE_BUY else 'Short'}\n"
            message += f"Stop Price: {stop_price}\n"
            message += f"Quantity: {abs(position)}\n"
            message += f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            send_telegram_message(message)
            logging.info(f"Stop loss placed at {stop_price}: {order}")
    except Exception as e:
        error_message = f"❌ <b>Stop Loss Error</b>\n"
        error_message += f"Symbol: {symbol}\n"
        error_message += f"Error: {str(e)}"
        send_telegram_message(error_message)
        logging.error(f"Error placing stop loss: {e}")

def on_message(ws, message):
    global prices, last_signal, last_websocket_message
    
    try:
        last_websocket_message = time.time()
        
        # Parse the websocket message
        data = json.loads(message)
        if 'k' not in data:  # Skip non-kline messages
            return
            
        price = Decimal(data['k']['c'])  # Closing price as Decimal
        
        # Add price to our list
        prices.append(float(price))  # Keep prices as float for pandas operations
        
        # Keep only the last long_window prices
        if len(prices) > long_window:
            prices = prices[-long_window:]
        
        # Only proceed if we have enough data points
        if len(prices) >= long_window:
            df = pd.Series(prices)
            short_ma = df.rolling(window=short_window, min_periods=1).mean().iloc[-1]
            long_ma = df.rolling(window=long_window, min_periods=1).mean().iloc[-1]
            
            # Calculate price movement for logging only
            price_movement = abs((short_ma - long_ma) / long_ma * 100)
            
            # Debug logging for MA calculations
            logging.info(f"📊 MA Debug - Short MA: {short_ma:.2f}, Long MA: {long_ma:.2f}, Movement: {price_movement:.2f}%")
            
            # Get current position
            position = get_position()
            
            # Calculate new position size
            quantity = Decimal(str(calculate_position_size())).quantize(Decimal('0.001'), rounding=ROUND_DOWN)
            
            # Determine current signal
            current_signal = 'long' if short_ma > long_ma else 'short'
            
            # If no position, open one based on current signal
            if position == Decimal('0'):
                if current_signal == 'long':
                    place_order(SIDE_BUY, float(quantity))  # Convert to float for API call
                    place_stop_loss(float(price), SIDE_BUY)
                elif current_signal == 'short':
                    place_order(SIDE_SELL, float(quantity))  # Convert to float for API call
                    place_stop_loss(float(price), SIDE_SELL)
                last_signal = current_signal
                logging.info(f"Opened new {current_signal} position")
            
            # Only change position on crossover (when signal changes)
            elif current_signal != last_signal:
                if current_signal == 'long':          # BUY signal
                    if position < Decimal('0'):  # If we have a short position
                        place_order(SIDE_BUY, float(abs(position)))    #close original order
                        place_order(SIDE_BUY, float(quantity))
                        place_stop_loss(float(price), SIDE_BUY)
                elif current_signal == 'short':        # Sell signal
                    if position > Decimal('0'):  # If we have a long position
                        place_order(SIDE_SELL, float(abs(position)))   #close original order
                        place_order(SIDE_SELL, float(quantity))
                        place_stop_loss(float(price), SIDE_SELL)
                
                # Update last signal
                last_signal = current_signal
                logging.info(f"Signal changed to: {current_signal}")
        else:
            logging.info(f"Waiting for more data points ({len(prices)}/{long_window})")
                
    except Exception as e:
        logging.error(f"Error in message handling: {e}")

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
            return Decimal(position_info[0]['positionAmt'])
        return Decimal('0')
    except Exception as e:
        print(f"Error getting position: {e}")
        return Decimal('0')

def place_order(side, quantity):
    try:
        # Log order attempt
        logging.info(f"🔄 Attempting to place order: Side={'BUY' if side == SIDE_BUY else 'SELL'}, Quantity={quantity}")
        
        try:
            order = client.futures_create_order(
                symbol=symbol,
                side=side,
                type=ORDER_TYPE_MARKET,
                quantity=quantity
            )
        except APIError as e:
            # Check if it's a timeout error (-1007)
            if e.code == -1007:
                logging.warning("⚠️ Order timeout (-1007) - Checking if order was actually placed...")
                
                # Wait a moment for the order to potentially process
                time.sleep(2)
                
                # Get current position to check if order went through
                current_position = get_position()
                expected_position = quantity if side == SIDE_BUY else -quantity
                
                # If position matches what we expected, order went through
                if abs(current_position) == abs(expected_position):
                    logging.info("✅ Order actually went through despite timeout")
                    message = f"🟢 <b>Order Placed (Recovered from Timeout)</b>\n"
                    message += f"Symbol: {symbol}\n"
                    message += f"Side: {'BUY' if side == SIDE_BUY else 'SELL'}\n"
                    message += f"Quantity: {quantity}\n"
                    message += f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    send_telegram_message(message)
                    return
                
                # If position didn't change, order didn't go through
                logging.error("❌ Order did not go through - will retry")
                raise e  # Re-raise to trigger retry
            
            # For other API errors, just raise them
            raise e
        except Exception as e:
            # For non-API errors, just raise them
            raise e
        
        # If we get here, order was successful
        message = f"🟢 <b>Order Placed</b>\n"
        message += f"Symbol: {symbol}\n"
        message += f"Side: {'BUY' if side == SIDE_BUY else 'SELL'}\n"
        message += f"Quantity: {quantity}\n"
        message += f"Price: {order.get('avgPrice', 'N/A')}\n"
        message += f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        send_telegram_message(message)
        logging.info(f"✅ Order placed successfully: {order}")
        
    except Exception as e:
        error_message = f"❌ <b>Order Error</b>\n"
        error_message += f"Symbol: {symbol}\n"
        error_message += f"Side: {'BUY' if side == SIDE_BUY else 'SELL'}\n"
        error_message += f"Error: {str(e)}"
        send_telegram_message(error_message)
        logging.error(f"❌ Failed to place order: {str(e)}")

def close_all_positions():
    try:
        position = get_position()
        if position > 0:  # Long position
            place_order(SIDE_SELL, abs(position))
            message = f"🔴 <b>Position Closed</b>\n"
            message += f"Symbol: {symbol}\n"
            message += f"Type: Long\n"
            message += f"Quantity: {abs(position)}\n"
            message += f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            send_telegram_message(message)
            logging.info("Closed long position")
        elif position < 0:  # Short position
            place_order(SIDE_BUY, abs(position))
            message = f"🔴 <b>Position Closed</b>\n"
            message += f"Symbol: {symbol}\n"
            message += f"Type: Short\n"
            message += f"Quantity: {abs(position)}\n"
            message += f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            send_telegram_message(message)
            logging.info("Closed short position")
        else:
            message = "ℹ️ No positions to close"
            send_telegram_message(message)
            logging.info("No positions to close")
    except Exception as e:
        error_message = f"❌ <b>Error Closing Position</b>\n"
        error_message += f"Symbol: {symbol}\n"
        error_message += f"Error: {str(e)}"
        send_telegram_message(error_message)
        logging.error(f"Error closing positions: {e}")

async def run_telegram_bot():
        
    global telegram_app
    telegram_app = Application.builder().token(TELEGRAM_TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("status", check_position_command))
    telegram_app.add_handler(CallbackQueryHandler(button_callback))
    
    # Start the bot
    await telegram_app.initialize()
    await telegram_app.start()
    await telegram_app.run_polling()

def run_trading_bot():
    """Run the trading bot"""
    # Send startup message
    startup_message = f"🚀 <b>Trading Bot Started</b>\n"
    startup_message += f"Symbol: {symbol}\n"
    startup_message += f"Leverage: {leverage}x\n"
    startup_message += f"Account Usage: {ACCOUNT_USAGE_PERCENTAGE}%\n"
    startup_message += f"Stop Loss: {STOP_LOSS_PERCENTAGE}%\n"
    startup_message += f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    send_telegram_message(startup_message)
    
    while True:  # Main loop for reconnection
        try:
            # Setup leverage and margin type
            setup_leverage()
            
            # Initialize websocket connection
            websocket.enableTrace(False)  # Disable WebSocket trace messages
            ws = websocket.WebSocketApp(
                f"wss://fstream.binance.com/ws/{symbol.lower()}@kline_{timeframe}",
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
                on_open=on_open
            )
            
            # Start connection monitor thread
            def connection_monitor():
                while True:
                    time.sleep(10)  # Check every 10 seconds
                    current_time = time.time()
                    if current_time - last_websocket_message > WEBSOCKET_TIMEOUT:
                        message = "⚠️ <b>WebSocket Connection Lost</b>\n"
                        message += f"Last message received: {datetime.fromtimestamp(last_websocket_message).strftime('%Y-%m-%d %H:%M:%S')}\n"
                        message += "Attempting to reconnect..."
                        send_telegram_message(message)
                        logging.warning(f"WebSocket connection stale (last message: {last_websocket_message}), restarting...")
                        ws.close()
                        break
            
            monitor_thread = threading.Thread(target=connection_monitor)
            monitor_thread.daemon = True
            monitor_thread.start()
            
            # Run websocket with ping settings
            ws.run_forever(ping_interval=20, ping_timeout=10)
            
        except KeyboardInterrupt:
            message = "🛑 <b>Bot Stopped by User</b>\nClosing all positions..."
            send_telegram_message(message)
            logging.info("\nProgram terminated by user")
            close_all_positions()
            message = "✅ <b>Bot Stopped</b>\nAll positions closed successfully."
            send_telegram_message(message)
            logging.info("All positions closed. Program ended.")
            break
        except Exception as e:
            error_message = f"❌ <b>Unexpected Error</b>\n{str(e)}\nAttempting to restart in 60 seconds..."
            send_telegram_message(error_message)
            logging.error(f"Unexpected error: {e}")
            close_all_positions()
            logging.info("All positions closed. Attempting to restart in 60 seconds...")
            time.sleep(60)  # Wait before reconnecting

async def main():
    """Main async function to run all components"""
    # Start Flask in a separate thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Start Telegram bot
    telegram_task = asyncio.create_task(run_telegram_bot())
    
    # Start trading bot in a separate thread
    trading_thread = threading.Thread(target=run_trading_bot, daemon=True)
    trading_thread.start()
    
    # Wait for Telegram bot to complete (it won't unless there's an error)
    await telegram_task

if __name__ == "__main__":
    # Create event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        # Run main async function
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logging.info("Shutting down...")
    finally:
        loop.close()
