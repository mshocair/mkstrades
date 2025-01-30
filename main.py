import os
import json
import traceback
import datetime
from flask import Flask, request
from google.oauth2 import service_account
from googleapiclient.discovery import build
import requests
from googleapiclient.errors import HttpError

app = Flask(__name__)

# Environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "")
SERVICE_ACCOUNT_FILE = os.getenv("SERVICE_ACCOUNT_FILE", "service_account.json")

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

# [Keep all existing functions below exactly as they were]
def process_average_command(command):
    try:
        parts = command.split(" ")
        if len(parts) != 2:
            return "Invalid format. Use: /average COIN"

        coin = parts[1].upper()
        avg_price, error = get_average_buy_price(coin)
        
        if error:
            return f"Can't calculate average for {coin}: {error}"
        elif avg_price is None:
            return f"No buy transactions found for {coin}"
            
        return f"Average buy price for {coin}: ${avg_price:.2f}"
    except Exception as e:
        traceback.print_exc()
        return f"Error processing /average command: {e}"

# Updated get_average_buy_price function
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

# Updated holdings functions
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
# [NO CHANGES NEEDED BELOW THIS LINE]

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
