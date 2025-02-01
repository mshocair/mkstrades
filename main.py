import os
import json
import traceback
import datetime
import time
import logging
from threading import Thread
from flask import Flask, request
from google.oauth2 import service_account
from googleapiclient.discovery import build
import requests
from googleapiclient.errors import HttpError
from requests.exceptions import RequestException
from requests.adapters import HTTPAdapter, Retry

app = Flask(__name__)

# ==================== Configuration ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "")
SERVICE_ACCOUNT_FILE = os.getenv("SERVICE_ACCOUNT_FILE", "service_account.json")

# API configuration
COINGECKO_API = "https://api.coingecko.com/api/v3"
KUCOIN_API = "https://api.kucoin.com/api/v1"

# We no longer store a static dictionary for every coin manually.
# Instead, we'll dynamically build one at startup:
COINGECKO_SYMBOL_MAP = {}

# Sheet configuration
TRADES_HEADER = ["Coin", "Current Price (CG)", "KuCoin Price", "Last Updated"]
HEADERS = [
    "Timestamp", "Person", "Coin", "Price",
    "Quantity", "Exchange", "Total", "Type"
]

# Configure HTTP session
session = requests.Session()
retries = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"]
)
session.mount('https://', HTTPAdapter(max_retries=retries))

# ==================== Google Sheets Setup ====================
def get_sheets_service():
    try:
        creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        return build("sheets", "v4", credentials=creds)
    except Exception as e:
        logger.error(f"Failed to initialize Sheets service: {e}")
        raise

sheets_service = get_sheets_service()

# ==================== Build Symbol → ID Map ====================
def build_coingecko_symbol_map():
    """
    Fetch the full list of coins from CoinGecko and build a dict:
    symbol_map[symbol.lower()] = coin_id
    
    If multiple coins share the same symbol, this picks the first encountered.
    """
    url = f"{COINGECKO_API}/coins/list"
    try:
        logger.info("Fetching all coins list from CoinGecko...")
        response = session.get(url, timeout=30)
        response.raise_for_status()
        all_coins = response.json()  # list of dicts: {id, symbol, name}
        
        symbol_map = {}
        for coin_info in all_coins:
            symbol = coin_info["symbol"].lower()
            # Only set if not already mapped, so first match "wins"
            if symbol not in symbol_map:
                symbol_map[symbol] = coin_info["id"]
        
        logger.info(f"CoinGecko symbol map built. Total unique symbols: {len(symbol_map)}")
        return symbol_map
    except Exception as e:
        logger.error(f"Error building symbol map from CoinGecko: {e}")
        return {}

# ==================== Price Tracking System ====================
def initialize_trades_sheet():
    try:
        if not sheet_exists("Trades"):
            sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=SPREADSHEET_ID,
                body={
                    "requests": [{
                        "addSheet": {
                            "properties": {
                                "title": "Trades",
                                "gridProperties": {"rowCount": 1000, "columnCount": 4}
                            }
                        }
                    }]
                }
            ).execute()
            logger.info("Created Trades sheet")

        sheets_service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range="Trades!A1",
            valueInputOption="RAW",
            body={"values": [TRADES_HEADER]}
        ).execute()
        logger.info("Initialized Trades sheet")
    except Exception as e:
        logger.error(f"Error initializing Trades sheet: {e}")
        raise

def price_updater():
    initialize_trades_sheet()
    logger.info("Starting price updater")
    while True:
        try:
            update_trades_sheet()
            time.sleep(600)  # 10 minutes
        except Exception as e:
            logger.error(f"Price update failed: {e}")
            time.sleep(60)

def update_trades_sheet():
    try:
        master_coins = get_unique_coins_from_master()
        existing_coins = get_existing_trades_entries()
        new_coins = [coin for coin in master_coins if coin not in existing_coins]

        if new_coins:
            sheets_service.spreadsheets().values().append(
                spreadsheetId=SPREADSHEET_ID,
                range="Trades!A2",
                valueInputOption="USER_ENTERED",
                body={"values": [[coin] for coin in new_coins]}
            ).execute()
            logger.info(f"Added new coins: {new_coins}")

        refresh_all_prices()
    except Exception as e:
        logger.error(f"Trades update error: {e}")
        raise

def get_unique_coins_from_master():
    try:
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Master!C2:F",
            valueRenderOption="UNFORMATTED_VALUE"
        ).execute()
        
        coins = set()
        for row in result.get('values', []):
            if len(row) >= 4 and row[0].strip():
                coins.add(row[0].upper().strip())
        return list(coins)
    except Exception as e:
        logger.error(f"Error reading Master sheet: {e}")
        return []

def get_existing_trades_entries():
    """
    Reads the Trades sheet (column A) to see which coins are already listed.
    Returns a list of uppercase coin symbols.
    """
    try:
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Trades!A2:A", 
            valueRenderOption="UNFORMATTED_VALUE"
        ).execute()

        existing_coins = set()
        for row in result.get('values', []):
            if row and row[0]:
                existing_coins.add(row[0].upper().strip())
        return list(existing_coins)
    except Exception as e:
        logger.error(f"Error reading existing coins from Trades sheet: {e}")
        return []

def refresh_all_prices():
    try:
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Trades!A2:D",
            valueRenderOption="UNFORMATTED_VALUE"
        ).execute()
        
        updates = []
        rows = result.get('values', [])
        for i, row in enumerate(rows):
            if not row or not row[0].strip():
                continue
            
            coin = row[0].upper().strip()
            gecko_price = get_coingecko_price(coin)
            kucoin_price = get_kucoin_price(coin) if needs_kucoin_price(coin) else ""
            
            updates.append({
                "range": f"Trades!B{i+2}:D{i+2}",
                "values": [[
                    gecko_price,
                    kucoin_price,
                    datetime.datetime.now().isoformat()
                ]]
            })

        if updates:
            sheets_service.spreadsheets().values().batchUpdate(
                spreadsheetId=SPREADSHEET_ID,
                body={"data": updates, "valueInputOption": "USER_ENTERED"}
            ).execute()
            logger.info(f"Updated {len(updates)} prices")
    except Exception as e:
        logger.error(f"Price refresh error: {e}")
        raise

# ============= Modified get_coingecko_price =============
def get_coingecko_price(coin_symbol):
    """
    Uses our global COINGECKO_SYMBOL_MAP to find the correct coin ID.
    Then queries the /simple/price endpoint for USD value.
    """
    try:
        symbol_lower = coin_symbol.lower()
        coin_id = COINGECKO_SYMBOL_MAP.get(symbol_lower)

        if not coin_id:
            # We have no known ID for this symbol
            logger.error(f"CoinGecko: No matching ID for symbol '{coin_symbol}'")
            return "N/A"

        response = session.get(
            f"{COINGECKO_API}/simple/price",
            params={"ids": coin_id, "vs_currencies": "usd"},
            timeout=10
        )
        response.raise_for_status()
        data = response.json()

        # e.g. data = {"dogecoin":{"usd":0.08}}
        return float(data[coin_id]["usd"])
    except Exception as e:
        logger.error(f"CoinGecko error for {coin_symbol}: {e}")
        return "N/A"

def get_kucoin_price(coin):
    try:
        response = session.get(
            f"{KUCOIN_API}/market/orderbook/level1",
            params={"symbol": f"{coin}-USDT"},
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        return float(data["data"]["price"])
    except Exception as e:
        logger.error(f"KuCoin error for {coin}: {e}")
        return "N/A"

def needs_kucoin_price(coin):
    try:
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Master!C2:F",
            valueRenderOption="UNFORMATTED_VALUE"
        ).execute()
        
        for row in result.get('values', []):
            if len(row) >= 4:
                if row[0].upper().strip() == coin.upper() and row[3].lower().strip() == "kucoin":
                    return True
        return False
    except Exception as e:
        logger.error(f"KuCoin check error: {e}")
        return False

# ==================== Telegram Bot ====================
def get_main_keyboard():
    return {
        "keyboard": [
            [{"text": "/add"}, {"text": "/average"}],
            [{"text": "/holdings"}, {"text": "/help"}]
        ],
        "resize_keyboard": True,
        "persistent": True
    }

def get_inline_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "➕ Add Trade", "callback_data": "/add"}],
            [
                {"text": "📊 Holdings", "callback_data": "/holdings"},
                {"text": "💵 Average Price", "callback_data": "/average"}
            ]
        ]
    }

@app.route("/")
def index():
    return "Crypto Tracker Bot - Active"

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    try:
        update = request.get_json()
        
        if 'callback_query' in update:
            callback = update['callback_query']
            chat_id = callback['message']['chat']['id']
            data = callback['data']
            
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery",
                json={"callback_query_id": callback['id']}
            )
            
            if data == "/add":
                send_message(chat_id, "📝 Format:\n/add PERSON COIN PRICE QTY EXCHANGE BUY/SELL")
            elif data == "/average":
                send_message(chat_id, "🔢 Enter:\n/average COIN")
            elif data == "/holdings":
                send_message(chat_id, "📈 Choose:\n/holdings COIN\nor\n/holdings PERSON COIN")
            
            return "ok", 200

        message = update.get("message")
        if not message:
            return "ok", 200

        chat_id = message["chat"]["id"]
        text = message.get("text", "").strip()

        if text.startswith("/start"):
            send_message(chat_id, "🤖 Welcome!", get_inline_keyboard())
            send_message(chat_id, "🛠️ Commands:", get_main_keyboard())
        elif text.startswith("/help"):
            send_message(chat_id, """📚 Commands:
/add - Record new trade
/average - Check average price
/holdings - View holdings""")
        elif text.startswith("/add"):
            response = process_add_command(text)
            send_message(chat_id, response)
        elif text.startswith("/average"):
            response = process_average_command(text)
            send_message(chat_id, response)
        elif text.startswith("/holdings"):
            response = process_holdings_command(text)
            send_message(chat_id, response)
        else:
            send_message(chat_id, "❌ Unknown command. Use /help")

        return "ok", 200
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return "error", 500

def send_message(chat_id, text, reply_markup=None):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logger.error(f"Message send error: {e}")

# ==================== Trade Processing ====================
def process_add_command(command):
    try:
        parts = command.split()
        if len(parts) != 7:
            return "❌ Format: /add PERSON COIN PRICE QTY EXCHANGE BUY/SELL"

        _, person, coin, price, qty, exchange, order_type = parts
        person = person.lower()
        coin = coin.upper()
        order_type = order_type.upper()

        if order_type not in ["BUY", "SELL"]:
            return "❌ Use BUY/SELL"

        try:
            price = float(price)
            qty = float(qty)
            if price <= 0 or qty <= 0:
                raise ValueError
        except ValueError:
            return "❌ Invalid numbers"

        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        new_row = [timestamp, person, coin, price, qty, exchange, price*qty, order_type]

        for sheet in ["Master", coin, person]:
            create_sheet_if_not_exists(sheet)
            sheets_service.spreadsheets().values().append(
                spreadsheetId=SPREADSHEET_ID,
                range=f"{sheet}!A2",
                valueInputOption="USER_ENTERED",
                body={"values": [new_row]}
            ).execute()

        return f"✅ {person} {order_type.lower()} {qty} {coin} @ ${price} on {exchange}"
    except Exception as e:
        logger.error(f"Add command error: {e}")
        return "❌ Processing error"

def process_average_command(command):
    try:
        coin = command.split()[1].upper()
        avg_price, error = get_average_buy_price(coin)
        return f"📊 {coin} avg: ${avg_price:.2f}" if avg_price else f"❌ {error}"
    except Exception as e:
        logger.error(f"Average error: {e}")
        return "❌ Calculation failed"

def process_holdings_command(command):
    try:
        parts = command.split()
        if len(parts) == 2:
            return calculate_holdings(coin=parts[1].upper())
        elif len(parts) == 3:
            return calculate_holdings(person=parts[1].lower(), coin=parts[2].upper())
        else:
            return "❌ Use: /holdings COIN or /holdings PERSON COIN"
    except Exception as e:
        logger.error(f"Holdings error: {e}")
        return "❌ Calculation failed"

# ==================== Sheet Utilities ====================
def create_sheet_if_not_exists(sheet_name):
    try:
        if not sheet_exists(sheet_name):
            sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=SPREADSHEET_ID,
                body={
                    "requests": [{
                        "addSheet": {
                            "properties": {
                                "title": sheet_name,
                                "gridProperties": {"rowCount": 1000, "columnCount": 8}
                            }
                        }
                    }]
                }
            ).execute()
            
            sheets_service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=f"{sheet_name}!A1",
                valueInputOption="RAW",
                body={"values": [HEADERS]}
            ).execute()
    except HttpError as e:
        if "already exists" not in str(e):
            logger.error(f"Sheet creation error: {e}")
    except Exception as e:
        logger.error(f"Sheet error: {e}")

def sheet_exists(sheet_name):
    try:
        spreadsheet = sheets_service.spreadsheets().get(
            spreadsheetId=SPREADSHEET_ID,
            fields="sheets(properties(title))"
        ).execute()
        return any(s["properties"]["title"] == sheet_name for s in spreadsheet.get("sheets", []))
    except Exception as e:
        logger.error(f"Sheet check failed: {e}")
        return False

def get_average_buy_price(coin, person=None):
    try:
        sheet_name = person or coin
        if not sheet_exists(sheet_name):
            return None, f"{'Person' if person else 'Coin'} not found"

        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{sheet_name}!A2:H",
            valueRenderOption="UNFORMATTED_VALUE"
        ).execute()
        
        total_cost = 0.0
        total_qty = 0.0
        
        for row in result.get('values', []):
            if len(row) >= 8 and row[7].upper() == "BUY":
                if person and row[2].upper() != coin:
                    continue
                    
                try:
                    total = float(row[6])
                    qty = float(row[4])
                    total_cost += total
                    total_qty += qty
                except (ValueError, IndexError):
                    continue

        return (total_cost / total_qty, None) if total_qty > 0 else (None, "No buys found")
    except Exception as e:
        return None, str(e)

def calculate_holdings(coin=None, person=None):
    try:
        sheet_name = person or coin
        if not sheet_exists(sheet_name):
            return f"❌ {'Person' if person else 'Coin'} not found"

        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{sheet_name}!A2:H",
            valueRenderOption="UNFORMATTED_VALUE"
        ).execute()
        
        total_qty = 0.0
        for row in result.get('values', []):
            if len(row) >= 8:
                # If person is specified, ensure row's coin matches
                if person and row[2].upper() != coin:
                    continue
                    
                try:
                    qty = float(row[4])
                    if row[7].upper() == "BUY":
                        total_qty += qty
                    elif row[7].upper() == "SELL":
                        total_qty -= qty
                except (ValueError, IndexError):
                    continue

        avg_price, error = get_average_buy_price(coin, person)
        usd_value = total_qty * avg_price if avg_price else None
        
        response = f"📦 Holdings for {person+' ' if person else ''}{coin}: {total_qty:.4f}"
        if usd_value:
            response += f"\n💵 USD Value: ${usd_value:.2f}"
        elif error:
            response += f"\n⚠️ Value calculation failed ({error})"
            
        return response
    except Exception as e:
        return f"❌ Error: {str(e)}"

# ==================== Main ====================
if __name__ == "__main__":
    # 1) Build the CoinGecko symbol map once on startup
    COINGECKO_SYMBOL_MAP = build_coingecko_symbol_map()

    # 2) Initialize Trades sheet and start background price updater
    initialize_trades_sheet()
    Thread(target=price_updater, daemon=True).start()

    # 3) Run Flask
    app.run(host="0.0.0.0", port=5000)
