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
COIN_ID_MAPPING = {
    'BTC': 'bitcoin',
    'ETH': 'ethereum',
    'USDT': 'tether',
    # Add more coin mappings as needed
}

# Sheet configuration
TRADES_HEADER = ["Coin", "Current Price (CG)", "KuCoin Price", "Last Updated"]

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
if not os.path.exists(SERVICE_ACCOUNT_FILE):
    raise ValueError(f"Service account file not found: {SERVICE_ACCOUNT_FILE}")

try:
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    sheets_service = build("sheets", "v4", credentials=creds)
except Exception as e:
    logger.error(f"Failed to load service account credentials: {e}")
    raise

# ==================== Price Tracking System ====================
def price_updater():
    """Background price update thread"""
    logger.info("Starting price updater")
    while True:
        try:
            update_trades_sheet()
        except Exception as e:
            logger.error(f"Price update failed: {e}")
        time.sleep(600)

def update_trades_sheet():
    """Update trades sheet with current prices"""
    try:
        master_coins = get_unique_coins_from_master()
        existing_coins = get_existing_trades_entries()
        new_coins = {k:v for k,v in master_coins.items() if k not in existing_coins}
        
        if new_coins:
            logger.info(f"Adding {len(new_coins)} new coins")
            update_trades_entries(new_coins)
        
        refresh_all_prices()
    except Exception as e:
        logger.error(f"Trades sheet update failed: {e}")

def get_unique_coins_from_master():
    """Get coins from Master sheet"""
    try:
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Master!C2:F",
            valueRenderOption="UNFORMATTED_VALUE"
        ).execute()
        
        coins = {}
        for row in result.get('values', []):
            if len(row) >= 4:
                coin = row[0].upper().strip()
                exchange = row[3].lower().strip()
                if coin and exchange:
                    coins.setdefault(coin, set()).add(exchange)
        return coins
    except Exception as e:
        logger.error(f"Error reading Master sheet: {e}")
        return {}

def refresh_all_prices():
    """Update all prices in Trades sheet"""
    try:
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Trades!A2:D",
            valueRenderOption="UNFORMATTED_VALUE"
        ).execute()
        
        updates = []
        for i, row in enumerate(result.get('values', [])):
            if len(row) < 4:
                continue
                
            coin = row[0].upper().strip()
            if not coin:
                continue
                
            gecko_price = get_coingecko_price(coin)
            kucoin_price = get_kucoin_price(coin) if needs_kucoin_price(coin) else ""
            
            updates.append({
                "range": f"Trades!B{i+2}:D{i+2}",
                "values": [[
                    gecko_price if isinstance(gecko_price, float) else "N/A",
                    kucoin_price if isinstance(kucoin_price, float) else "",
                    datetime.datetime.now().isoformat()
                ]]
            })
        
        if updates:
            sheets_service.spreadsheets().values().batchUpdate(
                spreadsheetId=SPREADSHEET_ID,
                body={"data": updates, "valueInputOption": "USER_ENTERED"}
            ).execute()
            
    except Exception as e:
        logger.error(f"Price refresh failed: {e}")

def get_coingecko_price(coin):
    """Get price from CoinGecko"""
    try:
        coin_id = COIN_ID_MAPPING.get(coin.upper(), coin.lower())
        response = session.get(
            f"{COINGECKO_API}/simple/price",
            params={"ids": coin_id, "vs_currencies": "usd"},
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        return float(data[coin_id]["usd"])
    except Exception as e:
        logger.error(f"CoinGecko error for {coin}: {e}")
        return "N/A"

def get_kucoin_price(coin):
    """Get price from KuCoin"""
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
    """Check if coin has KuCoin trades"""
    master_coins = get_unique_coins_from_master()
    return "kucoin" in master_coins.get(coin.upper(), set())

# ==================== Telegram Bot Functions ====================
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
        
        # Handle inline keyboard
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

        # Handle regular messages
        message = update.get("message")
        if not message:
            return "ok", 200

        chat_id = message["chat"]["id"]
        text = message.get("text", "").strip()

        if text.startswith("/start"):
            send_message(chat_id, "🤖 Welcome!", get_inline_keyboard())
            send_message(chat_id, "🛠️ Commands:", get_main_keyboard())
        elif text.startswith("/help"):
            help_text = """📚 Commands:
/add - New trade
/average - Avg price
/holdings - Portfolio
📱 Use buttons or commands!"""
            send_message(chat_id, help_text)
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
    """Send Telegram message"""
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
        logger.error(f"Message send failed: {e}")

# ==================== Trade Processing ====================
def process_add_command(command):
    """Process /add command"""
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
    """Process /average command"""
    try:
        coin = command.split()[1].upper()
        avg_price, error = get_average_buy_price(coin)
        return f"📊 {coin} avg: ${avg_price:.2f}" if avg_price else f"❌ {error}"
    except Exception as e:
        logger.error(f"Average error: {e}")
        return "❌ Calculation failed"

def process_holdings_command(command):
    """Process /holdings command"""
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

# ==================== Sheet Management ====================
def create_sheet_if_not_exists(sheet_name):
    """Create sheet if missing"""
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
    """Check sheet existence"""
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
    """Calculate average buy price for a coin"""
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
    """Calculate holdings for coin or person+coin"""
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
                if person and row[2].upper() != coin:
                    continue
                    
                try:
                    qty = float(row[4])
                    total_qty += qty if row[7].upper() == "BUY" else -qty
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
    # Initialize sheets
    for sheet in ["Master", "Trades"]:
        create_sheet_if_not_exists(sheet)
    
    # Ensure Trades headers
    try:
        sheets_service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range="Trades!A1",
            valueInputOption="RAW",
            body={"values": [TRADES_HEADER]}
        ).execute()
    except Exception as e:
        logger.error(f"Trades header error: {e}")

    # Start services
    Thread(target=price_updater, daemon=True).start()
    app.run(host="0.0.0.0", port=5000)
