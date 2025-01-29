import os
import json
import traceback
import datetime
from flask import Flask, request
from google.oauth2 import service_account
from googleapiclient.discovery import build
import requests

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

@app.route("/")
def index():
    return "Hello from Render + Python + Google Sheets!"

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    try:
        update = request.get_json()
        print(f"Received update: {update}")

        message = update.get("message")
        if not message:
            return "ok", 200

        chat_id = message.get("chat", {}).get("id")
        text = message.get("text", "")

        if text.startswith("/start"):
            send_telegram_message(chat_id, "Welcome to Crypto Tracker Bot!\nCommands:\n/add PERSON COIN PRICE QUANTITY EXCHANGE BUY/SELL\n/average COIN\n/holdings COIN\n/holdings PERSON COIN")
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
            send_telegram_message(chat_id, "Unknown command. Use /start, /add, /average, or /holdings.")

        return "ok", 200
    except Exception as e:
        traceback.print_exc()
        print(f"Error in telegram_webhook: {e}")
        return "error", 500

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

        create_sheet_if_not_exists("Master")
        create_sheet_if_not_exists(coin)
        create_sheet_if_not_exists(person)

        append_to_sheet("Master", new_row)
        append_to_sheet(coin, new_row)
        append_to_sheet(person, new_row)

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

def calculate_average(coin):
    """Calculate the average buy price for a coin."""
    try:
        result = sheets_service.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range=f"{coin}!A2:H").execute()
        data = result.get("values", [])

        total_quantity = 0
        total_cost = 0

        for row in data:
            if len(row) < 8:
                continue
            if row[7].strip().upper() == "BUY":
                quantity = float(row[4])
                total_cost += float(row[6])
                total_quantity += quantity

        if total_quantity == 0:
            return f"📊 No valid entries for {coin}."

        average_price = total_cost / total_quantity
        return f"📊 Average price for {coin}: ${average_price:.2f} (Total held: {total_quantity})"
    except Exception as e:
        traceback.print_exc()
        return f"Error calculating average for {coin}: {e}"

def calculate_total_holdings_for_coin(coin):
    """Calculate total holdings for a specific coin."""
    try:
        result = sheets_service.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range=f"{coin}!A2:H").execute()
        data = result.get("values", [])

        total_quantity = 0
        for row in data:
            if len(row) < 8:
                continue
            quantity = float(row[4])
            if row[7].strip().upper() == "BUY":
                total_quantity += quantity
            elif row[7].strip().upper() == "SELL":
                total_quantity -= quantity

        return f"📊 Total holdings for {coin}: {total_quantity:.4f}"
    except Exception as e:
        traceback.print_exc()
        return f"Error calculating holdings for {coin}: {e}"

def send_telegram_message(chat_id, text):
    """Send a message back to the user via Telegram"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        if len(text) > 4096:
            chunks = [text[i:i+4096] for i in range(0, len(text), 4096)]
            for chunk in chunks:
                requests.post(url, json={"chat_id": chat_id, "text": chunk})
        else:
            requests.post(url, json={"chat_id": chat_id, "text": text})
    except Exception as e:
        print(f"Failed to send message: {e}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
