import os
import json
import traceback
import datetime  # Import datetime to generate timestamps
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
    """Process the /holdings command to calculate total holdings"""
    try:
        parts = command.split(" ")
        
        if len(parts) == 2:
            # Total holdings for a coin
            coin = parts[1].upper()
            return calculate_total_holdings_for_coin(coin)
        elif len(parts) == 3:
            # Total holdings for a person and a coin
            person = parts[1].lower()
            coin = parts[2].upper()
            return calculate_total_holdings_for_person_and_coin(person, coin)
        else:
            return "Invalid format. Use /holdings COIN or /holdings PERSON COIN"
    except Exception as e:
        traceback.print_exc()
        return f"Error processing /holdings command: {e}"


def calculate_total_holdings_for_coin(coin):
    """Calculate the total holdings for a specific coin"""
    try:
        # Retrieve data from the coin sheet
        sheet = sheets_service.spreadsheets()
        result = sheet.values().get(spreadsheetId=SPREADSHEET_ID, range=f"{coin}!A2:H").execute()
        data = result.get("values", [])

        total_quantity = 0
        total_cost = 0

        for row in data:
            quantity = float(row[4])
            price = float(row[3])

            if row[7].strip().upper() == "BUY":
                total_quantity += quantity
                total_cost += quantity * price
            elif row[7].strip().upper() == "SELL":
                total_quantity -= quantity
                total_cost -= quantity * price

        if total_quantity == 0:
            return f"📊 No holdings for {coin}."

        average_price = total_cost / total_quantity
        total_value_usd = total_quantity * average_price

        return f"📊 Holdings for {coin}:\n- Quantity: {total_quantity:.4f}\n- Total Value (USD): ${total_value_usd:.2f}\n- Average Price: ${average_price:.2f}"
    except Exception as e:
        traceback.print_exc()
        return f"Error calculating holdings for {coin}: {e}"


def calculate_total_holdings_for_person_and_coin(person, coin):
    """Calculate the total holdings for a specific person and coin"""
    try:
        # Retrieve data from the person's sheet
        sheet = sheets_service.spreadsheets()
        result = sheet.values().get(spreadsheetId=SPREADSHEET_ID, range=f"{person}!A2:H").execute()
        data = result.get("values", [])

        total_quantity = 0
        total_cost = 0

        for row in data:
            if row[2].strip().upper() == coin:
                quantity = float(row[4])
                price = float(row[3])

                if row[7].strip().upper() == "BUY":
                    total_quantity += quantity
                    total_cost += quantity * price
                elif row[7].strip().upper() == "SELL":
                    total_quantity -= quantity
                    total_cost -= quantity * price

        if total_quantity == 0:
            return f"📊 No holdings for {person} in {coin}."

        average_price = total_cost / total_quantity
        total_value_usd = total_quantity * average_price

        return f"📊 Holdings for {person} in {coin}:\n- Quantity: {total_quantity:.4f}\n- Total Value (USD): ${total_value_usd:.2f}\n- Average Price: ${average_price:.2f}"
    except Exception as e:
        traceback.print_exc()
        return f"Error calculating holdings for {person} in {coin}: {e}"


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
