import os
import json
import traceback
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
    """Handle incoming Telegram updates via webhook"""
    try:
        update = request.get_json()
        print(f"Received update: {update}")

        # Extract message and chat ID
        message = update.get("message")
        if not message:
            return "ok", 200

        chat_id = message.get("chat", {}).get("id")
        text = message.get("text", "")

        # Handle commands
        if text.startswith("/start"):
            send_telegram_message(chat_id, "Welcome to Crypto Tracker Bot!\nCommands:\n/add PERSON COIN PRICE QUANTITY EXCHANGE\n/average COIN")
        elif text.startswith("/add"):
            response = process_add_command(text)
            send_telegram_message(chat_id, response)
        elif text.startswith("/average"):
            response = process_average_command(text)
            send_telegram_message(chat_id, response)
        else:
            send_telegram_message(chat_id, "Unknown command. Use /start, /add, or /average.")

        return "ok", 200
    except Exception as e:
        traceback.print_exc()
        print(f"Error in telegram_webhook: {e}")
        return "error", 500


def process_add_command(command):
    """Process the /add command to record a trade"""
    try:
        parts = command.split(" ")
        if len(parts) != 6:
            return "Invalid format. Use: /add PERSON COIN PRICE QUANTITY EXCHANGE"

        person = parts[1]
        coin = parts[2].upper()
        price = float(parts[3])
        quantity = float(parts[4])
        exchange = parts[5]

        if price <= 0 or quantity <= 0:
            return "Invalid price/quantity. Use positive numbers."

        # Check for duplicates and record trade
        sheet = sheets_service.spreadsheets()
        result = sheet.values().get(spreadsheetId=SPREADSHEET_ID, range="Trades!A2:G").execute()
        existing_data = result.get("values", [])

        for row in existing_data:
            if (
                row[1].strip().lower() == person.strip().lower() and
                row[2].strip().upper() == coin and
                row[3] == str(price) and
                row[4] == str(quantity) and
                row[5].strip().lower() == exchange.strip().lower()
            ):
                return f"⚠️ Duplicate entry detected for {person} - {coin}. Trade not recorded."

        # Append the new row
        new_row = [person, coin, price, quantity, exchange, price * quantity]
        sheet.values().append(
            spreadsheetId=SPREADSHEET_ID,
            range="Trades!A2",
            valueInputOption="USER_ENTERED",
            body={"values": [new_row]}
        ).execute()

        return f"✅ Trade recorded: {person} bought {quantity} {coin} at ${price} on {exchange}."
    except Exception as e:
        traceback.print_exc()
        return f"Error processing /add command: {e}"


def process_average_command(command):
    """Process the /average command to calculate the average cost of a coin"""
    try:
        parts = command.split(" ")
        if len(parts) != 2:
            return "Invalid format. Use: /average COIN"

        coin = parts[1].upper()

        # Retrieve data from the sheet
        sheet = sheets_service.spreadsheets()
        result = sheet.values().get(spreadsheetId=SPREADSHEET_ID, range="Trades!A2:G").execute()
        data = result.get("values", [])

        total_quantity = 0
        total_cost = 0

        for row in data:
            if row[2].strip().upper() == coin:
                quantity = float(row[3])
                total_cost += float(row[5])
                total_quantity += quantity

        if total_quantity == 0:
            return f"📊 No valid entries for {coin}."

        average_price = total_cost / total_quantity
        return f"📊 Average price for {coin}: ${average_price:.2f} (Total held: {total_quantity})"
    except Exception as e:
        traceback.print_exc()
        return f"Error processing /average command: {e}"


def send_telegram_message(chat_id, text):
    """Send a message back to the user via Telegram"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
        response = requests.post(url, json=payload)
        response.raise_for_status()
    except Exception as e:
        print(f"Failed to send message: {e}")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
