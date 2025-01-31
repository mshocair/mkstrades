import os
import json
import traceback
import datetime
import time
from threading import Thread
from flask import Flask, request
from google.oauth2 import service_account
from googleapiclient.discovery import build
import requests
from googleapiclient.errors import HttpError
from requests.exceptions import RequestException

app = Flask(__name__)

# ==================== Configuration ====================
# Environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "")
SERVICE_ACCOUNT_FILE = os.getenv("SERVICE_ACCOUNT_FILE", "service_account.json")

# API endpoints
COINGECKO_API = "https://api.coingecko.com/api/v3"
KUCOIN_API = "https://api.kucoin.com/api/v1"

# Sheet configuration
TRADES_HEADER = ["Coin", "Current Price (CG)", "KuCoin Price", "Last Updated"]
MASTER_SHEET_GID = "1996633798"
TRADES_SHEET_GID = "0"

# ==================== Sheets Service Setup ====================
if not os.path.exists(SERVICE_ACCOUNT_FILE):
    raise ValueError(f"Service account file not found: {SERVICE_ACCOUNT_FILE}")

try:
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    sheets_service = build("sheets", "v4", credentials=creds)
except Exception as e:
    raise ValueError(f"Failed to load service account credentials: {e}")

# ==================== Price Tracking Functions ====================
def price_updater():
    """Background thread for price updates"""
    while True:
        try:
            update_trades_sheet()
            time.sleep(600)  # 10 minutes
        except Exception as e:
            print(f"Price updater error: {e}")
            time.sleep(60)

def update_trades_sheet():
    """Update trades sheet with current prices"""
    try:
        master_coins = get_unique_coins_from_master()
        existing_coins = get_existing_trades_entries()
        new_coins = [c for c in master_coins if c not in existing_coins]
        
        if new_coins:
            update_trades_entries(new_coins)
        
        refresh_all_prices()
    except Exception as e:
        print(f"Failed to update trades sheet: {e}")

def get_unique_coins_from_master():
    """Get unique coins from Master sheet"""
    try:
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"Master!C2:E",
            valueRenderOption="UNFORMATTED_VALUE"
        ).execute()
        
        coins = {}
        for row in result.get('values', []):
            if len(row) >= 3:
                coin = row[0].upper()
                exchange = row[2].lower()
                coins.setdefault(coin, set()).add(exchange)
        return coins
    except Exception as e:
        print(f"Error getting master coins: {e}")
        return {}

def get_existing_trades_entries():
    """Get existing coins in Trades sheet"""
    try:
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Trades!A2:A",
            valueRenderOption="UNFORMATTED_VALUE"
        ).execute()
        return [row[0].upper() for row in result.get('values', []) if row]
    except Exception as e:
        print(f"Error getting trades entries: {e}")
        return []

def update_trades_entries(new_coins):
    """Add new coins to Trades sheet"""
    try:
        values = [[coin] for coin in new_coins.keys()]
        sheets_service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range="Trades!A2",
            valueInputOption="USER_ENTERED",
            body={"values": values}
        ).execute()
        print(f"Added {len(values)} new coins to Trades sheet")
    except Exception as e:
        print(f"Failed to update trades entries: {e}")

def refresh_all_prices():
    """Refresh all prices in Trades sheet"""
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
                
            coin = row[0].upper()
            gecko_price = get_coingecko_price(coin)
            kucoin_price = get_kucoin_price(coin) if needs_kucoin_price(coin) else ""
            
            updates.append({
                "range": f"Trades!B{i+2}:D{i+2}",
                "values": [[gecko_price, kucoin_price, datetime.datetime.now().isoformat()]]
            })
        
        if updates:
            sheets_service.spreadsheets().values().batchUpdate(
                spreadsheetId=SPREADSHEET_ID,
                body={"data": updates, "valueInputOption": "USER_ENTERED"}
            ).execute()
    except Exception as e:
        print(f"Failed to refresh prices: {e}")

def get_coingecko_price(coin):
    """Get price from CoinGecko"""
    try:
        response = requests.get(
            f"{COINGECKO_API}/simple/price",
            params={"ids": coin.lower(), "vs_currencies": "usd"}
        )
        data = response.json()
        return float(data[coin.lower()]["usd"])
    except (RequestException, KeyError, ValueError):
        return "N/A"

def get_kucoin_price(coin):
    """Get price from KuCoin"""
    try:
        response = requests.get(
            f"{KUCOIN_API}/market/orderbook/level1",
            params={"symbol": f"{coin}-USDT"}
        )
        data = response.json()
        return float(data["data"]["price"])
    except (RequestException, KeyError, ValueError):
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
        
        if 'callback_query' in update:
            callback = update['callback_query']
            chat_id = callback['message']['chat']['id']
            data = callback['data']
            
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery",
                json={"callback_query_id": callback['id']}
            )
            
            if data == "/add":
                send_message(chat_id, "📝 Use format:\n/add PERSON COIN PRICE QTY EXCHANGE BUY/SELL")
            elif data == "/average":
                send_message(chat_id, "🔢 Enter coin:\n/average COIN")
            elif data == "/holdings":
                send_message(chat_id, "📈 Choose:\n/holdings COIN\nor\n/holdings PERSON COIN")
            
            return "ok", 200

        message = update.get("message")
        if not message:
            return "ok", 200

        chat_id = message["chat"]["id"]
        text = message.get("text", "")

        if text.startswith("/start"):
            send_message(chat_id, "🤖 Welcome to Crypto Tracker Bot!", get_inline_keyboard())
            send_message(chat_id, "🛠️ Quick commands:", get_main_keyboard())
        elif text.startswith("/help"):
            help_text = """📚 Available Commands:
/add - Record new trade
/average - Check average price
/holdings - View holdings
📱 Use buttons or type commands!"""
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
        traceback.print_exc()
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
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Failed to send message: {e}")

# ==================== Trade Processing Functions ====================
def process_add_command(command):
    try:
        parts = command.split()
        if len(parts) != 7:
            return "❌ Invalid format. Use:\n/add PERSON COIN PRICE QTY EXCHANGE BUY/SELL"

        _, person, coin, price, qty, exchange, order_type = parts
        person = person.lower()
        coin = coin.upper()
        order_type = order_type.upper()

        if order_type not in ["BUY", "SELL"]:
            return "❌ Invalid order type. Use BUY/SELL"

        try:
            price = float(price)
            qty = float(qty)
            if price <= 0 or qty <= 0:
                raise ValueError
        except ValueError:
            return "❌ Invalid price/quantity. Use positive numbers"

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

        return f"✅ Trade recorded: {person} {order_type.lower()} {qty} {coin} at ${price} on {exchange}"
    except Exception as e:
        traceback.print_exc()
        return f"❌ Error processing trade: {str(e)}"

def process_average_command(command):
    try:
        coin = command.split()[1].upper()
        avg_price, error = get_average_buy_price(coin)
        
        if error:
            return f"❌ Can't calculate average for {coin}: {error}"
        return f"📊 Average buy price for {coin}: ${avg_price:.2f}"
    except IndexError:
        return "❌ Invalid format. Use: /average COIN"
    except Exception as e:
        return f"❌ Error calculating average: {str(e)}"

def process_holdings_command(command):
    try:
        parts = command.split()
        
        if len(parts) == 2:
            coin = parts[1].upper()
            return calculate_holdings(coin=coin)
        elif len(parts) == 3:
            person, coin = parts[1].lower(), parts[2].upper()
            return calculate_holdings(person=person, coin=coin)
        else:
            return "❌ Invalid format. Use:\n/holdings COIN\nor\n/holdings PERSON COIN"
    except Exception as e:
        return f"❌ Error calculating holdings: {str(e)}"

# ==================== Sheets Utilities ====================
def create_sheet_if_not_exists(sheet_name):
    """Safely create sheet with headers if it doesn't exist"""
    try:
        # Check existence with case sensitivity
        if not sheet_exists(sheet_name):
            print(f"Creating sheet: {sheet_name}")
            # Add with conflict avoidance
            sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=SPREADSHEET_ID,
                body={
                    "requests": [{
                        "addSheet": {
                            "properties": {
                                "title": sheet_name,
                                "gridProperties": {
                                    "rowCount": 1000,
                                    "columnCount": 8
                                }
                            }
                        }
                    }]
                }
            ).execute()
            
            # Add headers
            sheets_service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=f"{sheet_name}!A1",
                valueInputOption="RAW",
                body={"values": [HEADERS]}
            ).execute()
            
    except HttpError as e:
        if "already exists" in str(e):
            print(f"Sheet {sheet_name} already exists, skipping creation")
        else:
            print(f"Error creating sheet {sheet_name}: {e}")
    except Exception as e:
        print(f"Unexpected error creating sheet {sheet_name}: {e}")

# Update headers constant
HEADERS = [
    "Timestamp", 
    "Person", 
    "Coin", 
    "Price", 
    "Quantity", 
    "Exchange", 
    "Total", 
    "Type"
]

def sheet_exists(sheet_name):
    """Check if sheet exists (case-sensitive)"""
    try:
        spreadsheet = sheets_service.spreadsheets().get(
            spreadsheetId=SPREADSHEET_ID
        ).execute()
        return any(sheet["properties"]["title"] == sheet_name 
                 for sheet in spreadsheet["sheets"])
    except Exception as e:
        print(f"Error checking sheet existence: {e}")
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

# ==================== Initialization ====================
def initialize_sheets():
    """Initialize required sheets"""
    for sheet in ["Master", "Trades"]:
        try:
            create_sheet_if_not_exists(sheet)
        except Exception as e:
            print(f"Error initializing {sheet} sheet: {e}")

if __name__ == "__main__":
    initialize_sheets()
    Thread(target=price_updater, daemon=True).start()
    app.run(host="0.0.0.0", port=5000)
