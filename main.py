import os
import json
import traceback
import datetime
from flask import Flask, request
from google.oauth2 import service_account
from googleapiclient.discovery import build
import requests
from googleapiclient.errors import HttpError
from apscheduler.schedulers.background import BackgroundScheduler
import atexit

app = Flask(__name__)

# Environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "")
SERVICE_ACCOUNT_FILE = os.getenv("SERVICE_ACCOUNT_FILE", "service_account.json")
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "")

# Initialize scheduler
scheduler = BackgroundScheduler()
atexit.register(lambda: scheduler.shutdown())

# Load Google Sheets API credentials
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

# ---------------------------
# Price Tracking Enhancements
# ---------------------------

COINGECKO_API_URL = "https://api.coingecko.com/api/v3"

def setup_daily_prices_sheet():
    """Create DailyPrices sheet with headers if it doesn't exist"""
    sheet_name = "DailyPrices"
    create_sheet_if_not_exists(sheet_name)
    
    headers_range = f"{sheet_name}!A1:D1"
    result = sheets_service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=headers_range
    ).execute()
    values = result.get('values', [])
    
    if not values:
        headers = ["Timestamp", "CoinSymbol", "CoinGeckoID", "PriceUSD"]
        sheets_service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=headers_range,
            valueInputOption="RAW",
            body={"values": [headers]}
        ).execute()

def check_coin_mapping(coin_symbol):
    """Check if a coin symbol exists in CoinMappings sheet"""
    mappings = get_coin_mappings()
    if not mappings.get(coin_symbol):
        send_telegram_message(
            ADMIN_CHAT_ID,
            f"⚠️ New coin detected! Please add mapping for {coin_symbol} to CoinMappings sheet"
        )
        return False
    return True

def get_coin_mappings():
    """Retrieve symbol to CoinGecko ID mappings from CoinMappings sheet"""
    sheet_name = "CoinMappings"
    create_sheet_if_not_exists(sheet_name)
    
    range_name = f"{sheet_name}!A2:B"
    result = sheets_service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=range_name
    ).execute()
    values = result.get('values', [])
    
    return {row[0].upper(): row[1].lower() for row in values if len(row) >= 2}

def get_unique_coins():
    """Extract unique coin symbols from all relevant sheets"""
    coins = set()
    
    # Check Master sheet
    master_coins = sheets_service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range="Master!C2:C"
    ).execute().get('values', [])
    coins.update(row[0].upper() for row in master_coins if row)
    
    # Check existing coin sheets
    spreadsheet = sheets_service.spreadsheets().get(
        spreadsheetId=SPREADSHEET_ID
    ).execute()
    sheets = spreadsheet.get('sheets', [])
    coins.update(s['properties']['title'] for s in sheets if s['properties']['title'] != "Master")
    
    return list(coins)

def fetch_coingecko_prices(coin_ids):
    """Fetch current USD prices from CoinGecko API"""
    headers = {"x-cg-pro-api-key": COINGECKO_API_KEY}
    params = {'ids': ','.join(coin_ids), 'vs_currencies': 'usd'}
    
    try:
        response = requests.get(
            f"{COINGECKO_API_URL}/simple/price",
            headers=headers,
            params=params,
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Price fetch error: {str(e)}")
        return None

def record_prices():
    """Main function to record prices to DailyPrices sheet"""
    try:
        setup_daily_prices_sheet()
        mappings = get_coin_mappings()
        coins = get_unique_coins()
        
        valid_coins = []
        for symbol in coins:
            if cg_id := mappings.get(symbol):
                valid_coins.append((symbol, cg_id))
        
        if not valid_coins:
            return

        # Batch process to handle API limits
        batch_size = 250  # CoinGecko's max per request
        for i in range(0, len(valid_coins), batch_size):
            batch = valid_coins[i:i+batch_size]
            coin_ids = [cg_id for _, cg_id in batch]
            
            if prices := fetch_coingecko_prices(coin_ids):
                timestamp = datetime.datetime.utcnow().isoformat()
                rows = [
                    [timestamp, symbol, cg_id, prices[cg_id]['usd']]
                    for symbol, cg_id in batch
                    if prices.get(cg_id, {}).get('usd')
                ]
                
                if rows:
                    sheets_service.spreadsheets().values().append(
                        spreadsheetId=SPREADSHEET_ID,
                        range="DailyPrices!A2",
                        valueInputOption="USER_ENTERED",
                        body={"values": rows}
                    ).execute()

    except Exception as e:
        traceback.print_exc()
        send_telegram_message(ADMIN_CHAT_ID, f"⚠️ Price update failed: {str(e)}")

# Schedule price updates every 5 minutes
scheduler.add_job(
    record_prices,
    'interval',
    minutes=5,
    next_run_time=datetime.datetime.now() + datetime.timedelta(seconds=10)
)

# ---------------------------
# Modified Trade Processing
# ---------------------------

def process_add_command(command):
    try:
        parts = command.split(" ")
        if len(parts) != 7:
            return "Invalid format. Use: /add PERSON COIN PRICE QUANTITY EXCHANGE BUY/SELL"

        coin = parts[2].upper()
        if not check_coin_mapping(coin):
            return f"✅ Trade recorded for {coin}, but missing CoinGecko mapping - admin notified"

        # Rest of your existing process_add_command logic
        # ... (keep all your existing trade processing code) ...

        return f"✅ Trade recorded: {person} {order_type.lower()} {quantity} {coin} at ${price} on {exchange}."
    except Exception as e:
        traceback.print_exc()
        return f"Error processing /add command: {e}"

# ---------------------------
# Existing Application Code 
# (Keep all your original code below)
# ---------------------------
# Keyboard markup functions
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
    return "Hello from Render + Python + Google Sheets!"

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    try:
        update = request.get_json()
        print(f"Received update: {update}")

        # Handle inline keyboard callbacks
        if 'callback_query' in update:
            callback = update['callback_query']
            chat_id = callback['message']['chat']['id']
            data = callback['data']
            
            # Send callback confirmation
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery",
                json={"callback_query_id": callback['id']}
            )
            
            # Process callback data
            if data == "/add":
                send_telegram_message(chat_id, "📝 Use format:\n/add PERSON COIN PRICE QTY EXCHANGE BUY/SELL")
            elif data == "/average":
                send_telegram_message(chat_id, "🔢 Enter coin:\n/average COIN")
            elif data == "/holdings":
                send_telegram_message(chat_id, "📈 Choose:\n/holdings COIN\nor\n/holdings PERSON COIN")
            
            return "ok", 200

        # Handle regular messages
        message = update.get("message")
        if not message:
            return "ok", 200

        chat_id = message.get("chat", {}).get("id")
        text = message.get("text", "")

        if text.startswith("/start"):
            send_telegram_message(chat_id, "🤖 Welcome to Crypto Tracker Bot!", get_inline_keyboard())
            send_telegram_message(chat_id, "🛠️ Quick commands:", get_main_keyboard())
        elif text.startswith("/help"):
            help_text = """📚 Available Commands:
/add - Record new trade
/average - Check average price
/holdings - View holdings

📱 Use buttons or type commands directly!"""
            send_telegram_message(chat_id, help_text)
        elif text.startswith("/add"):
            response = process_add_command(text)
            send_telegram_message(chat_id, response)
        elif text.startswith("/average"):
            response = process_average_command(text)
            send_telegram_message(chat_id, response)
        elif text.startswith("/holdings"):
            response = process_holdings_command(text)
            send_telegram_message(chat_id, response)
        else:
            send_telegram_message(chat_id, "❌ Unknown command. Use buttons or type /help")

        return "ok", 200
    except Exception as e:
        traceback.print_exc()
        print(f"Error in telegram_webhook: {e}")
        return "error", 500

def send_telegram_message(chat_id, text, reply_markup=None):
    """Send a message with optional keyboard"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        response = requests.post(url, json=payload)
        response.raise_for_status()
    except Exception as e:
        print(f"Failed to send message: {e}")

# Your existing functions (unchanged)
def process_add_command(command):
    try:
        parts = command.split(" ")
        if len(parts) != 7:
            return "Invalid format. Use: /add PERSON COIN PRICE QUANTITY EXCHANGE BUY/SELL"

        person = parts[1].lower()
        coin = parts[2].upper()
        price = float(parts[3])
        quantity = float(parts[4])
        exchange = parts[5]
        order_type = parts[6].upper()

        if order_type not in ["BUY", "SELL"]:
            return "Invalid order type. Use BUY or SELL."

        if price <= 0 or quantity <= 0:
            return "Invalid price/quantity. Use positive numbers."

        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        new_row = [timestamp, person, coin, price, quantity, exchange, price * quantity, order_type]

        # Ensure the master sheet exists with headers
        create_sheet_if_not_exists("Master")

        # Ensure coin and person sheets exist with headers
        create_sheet_if_not_exists(coin)
        create_sheet_if_not_exists(person)

        # Append to Master Sheet
        sheets_service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range="Master!A2",
            valueInputOption="USER_ENTERED",
            body={"values": [new_row]}
        ).execute()

        # Append to Coin Sheet
        sheets_service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{coin}!A2",
            valueInputOption="USER_ENTERED",
            body={"values": [new_row]}
        ).execute()

        # Append to Person Sheet
        sheets_service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{person}!A2",
            valueInputOption="USER_ENTERED",
            body={"values": [new_row]}
        ).execute()

        return f"✅ Trade recorded: {person} {order_type.lower()} {quantity} {coin} at ${price} on {exchange}."
    except Exception as e:
        traceback.print_exc()
        return f"Error processing /add command: {e}"

def process_average_command(command):
    try:
        parts = command.split(" ")
        if len(parts) != 2:
            return "Invalid format. Use: /average COIN"

        coin = parts[1].upper()
        return calculate_average(coin)
    except Exception as e:
        traceback.print_exc()
        return f"Error processing /average command: {e}"

def process_holdings_command(command):
    try:
        parts = command.split(" ")

        if len(parts) == 2:
            coin = parts[1].upper()
            return calculate_total_holdings_for_coin(coin)
        elif len(parts) == 3:
            person = parts[1].lower()
            coin = parts[2].upper()
            return calculate_total_holdings_for_person_and_coin(person, coin)
        else:
            return "Invalid format. Use /holdings COIN or /holdings PERSON COIN"
    except Exception as e:
        traceback.print_exc()
        return f"Error processing /holdings command: {e}"

def create_sheet_if_not_exists(sheet_name):
    try:
        spreadsheet = sheets_service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
        sheet_titles = [sheet["properties"]["title"] for sheet in spreadsheet.get("sheets", [])]

        if sheet_name not in sheet_titles:
            # Create the new sheet
            requests_body = {
                "requests": [
                    {
                        "addSheet": {
                            "properties": {
                                "title": sheet_name
                            }
                        }
                    }
                ]
            }
            sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=SPREADSHEET_ID, body=requests_body
            ).execute()
            
            # Add headers to the new sheet
            headers = ["Timestamp", "Person", "Coin", "Price", "Quantity", "Exchange", "Total", "Type"]
            sheets_service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=f"{sheet_name}!A1",
                valueInputOption="RAW",
                body={"values": [headers]}
            ).execute()
    except Exception as e:
        traceback.print_exc()

def sheet_exists(sheet_name):
    """Check if a sheet with given name exists"""
    try:
        spreadsheet = sheets_service.spreadsheets().get(
            spreadsheetId=SPREADSHEET_ID
        ).execute()
        return any(sheet["properties"]["title"].lower() == sheet_name.lower() 
                 for sheet in spreadsheet.get("sheets", []))
    except Exception as e:
        traceback.print_exc()
        return False

def calculate_average(coin):
    try:
        coin = coin.upper()
        avg_price, error = get_average_buy_price(coin)
        
        if error:
            return f"Can't calculate average for {coin}: {error}"
        elif avg_price is None:
            return f"No buy transactions found for {coin}"
            
        return f"Average buy price for {coin}: ${avg_price:.2f}"
    except Exception as e:
        traceback.print_exc()
        return f"Error calculating average: {e}"

def get_average_buy_price(coin, person=None):
    """Calculate average buy price for a coin (optionally filtered by person)"""
    try:
        if person:
            if not sheet_exists(person):
                return None, f"Person '{person}' not found"
            sheet_name = person
            range_name = f"{sheet_name}!A2:H"
            filter_coin = True
        else:
            if not sheet_exists(coin):
                return None, f"Coin '{coin}' not found"
            sheet_name = coin
            range_name = f"{sheet_name}!A2:H"
            filter_coin = False

        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=range_name,
            valueRenderOption="UNFORMATTED_VALUE"
        ).execute()
        values = result.get('values', [])

        total_cost = 0.0
        total_quantity = 0.0
        filtered_rows = []

        if person:
            filtered_rows = [
                row for row in values
                if len(row) >= 8 
                and row[2].upper() == coin 
                and row[7].upper() == "BUY"
            ]
        else:
            filtered_rows = [
                row for row in values
                if len(row) >= 8 
                and row[7].upper() == "BUY"
            ]

        for row in filtered_rows:
            try:
                total = float(row[6])
                quantity = float(row[4])
                total_cost += total
                total_quantity += quantity
            except (ValueError, IndexError):
                continue

        if total_quantity == 0:
            return None, "No BUY transactions found"

        return total_cost / total_quantity, None

    except HttpError as e:
        if e.resp.status == 404:
            return None, "Sheet not found"
        return None, f"API Error: {e}"
    except Exception as e:
        return None, f"Calculation Error: {e}"

def calculate_total_holdings_for_coin(coin):
    try:
        if not sheet_exists(coin):
            return f"Coin '{coin}' not found"

        sheet_name = coin
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{sheet_name}!A2:H",
            valueRenderOption="UNFORMATTED_VALUE"
        ).execute()
        values = result.get('values', [])

        total_quantity = 0.0
        for row in values:
            if len(row) >= 8:
                order_type = row[7].upper()
                try:
                    quantity = float(row[4])
                    total_quantity += quantity if order_type == "BUY" else -quantity
                except (ValueError, IndexError):
                    continue

        avg_price, avg_error = get_average_buy_price(coin)
        usd_value = total_quantity * avg_price if avg_price else None

        response = f"Total holdings for {coin}: {total_quantity:.8f}"
        if usd_value is not None:
            response += f"\nUSD Value: ${usd_value:.2f} (based on average buy price)"
        elif avg_error:
            response += f"\nUSD Value: Calculation failed ({avg_error})"
        
        return response

    except HttpError as e:
        return f"Error accessing sheet: {e}"
    except Exception as e:
        return f"Error calculating holdings: {e}"

def calculate_total_holdings_for_person_and_coin(person, coin):
    try:
        if not sheet_exists(person):
            return f"Person '{person}' not found"

        sheet_name = person
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{sheet_name}!A2:H",
            valueRenderOption="UNFORMATTED_VALUE"
        ).execute()
        values = result.get('values', [])

        total_quantity = 0.0
        for row in values:
            if len(row) >= 8 and row[2].upper() == coin:
                order_type = row[7].upper()
                try:
                    quantity = float(row[4])
                    total_quantity += quantity if order_type == "BUY" else -quantity
                except (ValueError, IndexError):
                    continue

        avg_price, avg_error = get_average_buy_price(coin, person=person)
        usd_value = total_quantity * avg_price if avg_price else None

        response = f"Total holdings for {person} in {coin}: {total_quantity:.8f}"
        if usd_value is not None:
            response += f"\nUSD Value: ${usd_value:.2f} (based on personal average buy price)"
        elif avg_error:
            response += f"\nUSD Value: Calculation failed ({avg_error})"
        
        return response

    except HttpError as e:
        return f"Error accessing sheet: {e}"
    except Exception as e:
        return f"Error calculating holdings: {e}"


if __name__ == "__main__":
    scheduler.start()
    app.run(host="0.0.0.0", port=5000, debug=False)
