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
            send_telegram_message(chat_id, "Welcome to Crypto Tracker Bot!\nCommands:\n/add PERSON COIN PRICE QUANTITY EXCHANGE BUY/SELL\n/average COIN")
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


def create_sheet_if_not_exists(sheet_name):
    """Create a new sheet if it does not exist"""
    try:
        # Get the list of existing sheets
        spreadsheet = sheets_service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
        sheet_titles = [sheet["properties"]["title"] for sheet in spreadsheet.get("sheets", [])]

        # Check if the sheet already exists
        if sheet_name not in sheet_titles:
            # Add a new sheet
            requests_body = {
                "requests": [
                    {
                        "addSheet": {
                            "properties": {
                                "title": sheet_name,
                                "gridProperties": {
                                    "rowCount": 1000,
                                    "columnCount": 8  # Columns: Timestamp, Person, Coin, Price, Quantity, Exchange, Total Cost, Order Type
                                }
                            }
                        }
                    }
                ]
            }
            sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=SPREADSHEET_ID, body=requests_body
            ).execute()

            # Add headers to the new sheet
            header_row = ["Timestamp", "Person", "Coin", "Price", "Quantity", "Exchange", "Total Cost", "Order Type"]
            sheets_service.spreadsheets().values().append(
                spreadsheetId=SPREADSHEET_ID,
                range=f"{sheet_name}!A1",
                valueInputOption="USER_ENTERED",
                body={"values": [header_row]}
            ).execute()

        return True
    except Exception as e:
        traceback.print_exc()
        return False


def process_add_command(command):
    """Process the /add command to record a trade with buy/sell tracking"""
    try:
        parts = command.split(" ")
        if len(parts) != 7:
            return "Invalid format. Use: /add PERSON COIN PRICE QUANTITY EXCHANGE BUY/SELL"

        person = parts[1]
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

        # Create sheets for Master, Coin, and Person if they don't exist
        create_sheet_if_not_exists("Master")
        create_sheet_if_not_exists(coin)
        create_sheet_if_not_exists(person)

        # New row data
        new_row = [timestamp, person, coin, price, quantity, exchange, price * quantity, order_type]

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
    """Process the /average command to calculate the average cost of a coin (only buy orders considered)"""
    try:
        parts = command.split(" ")
        if len(parts) != 2:
            return "Invalid format. Use: /average COIN"

        coin = parts[1].upper()

        # Retrieve data from the coin sheet
        sheet = sheets_service.spreadsheets()
        result = sheet.values().get(spreadsheetId=SPREADSHEET_ID, range=f"{coin}!A2:H").execute()
        data = result.get("values", [])

        total_quantity = 0
        total_cost = 0

        for row in data:
            if row[7].strip().upper() == "BUY":
                quantity = float(row[4])
                total_cost += float(row[6])
                total_quantity += quantity

        if total_quantity == 0:
            return f"📊 No valid buy entries for {coin}."

        average_price = total_cost / total_quantity
        return f"📊 Average buy price for {coin}: ${average_price:.2f} (Total held: {total_quantity})"
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

