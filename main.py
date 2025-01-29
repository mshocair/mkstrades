import os
import json
import traceback
import datetime
from flask import Flask, request
from google.oauth2 import service_account
from googleapiclient.discovery import build
import requests

app = Flask(__name__)

# ---------------- ENVIRONMENT VARIABLES ---------------- #
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "")
SERVICE_ACCOUNT_FILE = os.getenv("SERVICE_ACCOUNT_FILE", "service_account.json")

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN is missing. Please set it in environment variables!")

# ---------------- GOOGLE SHEETS SETUP ---------------- #
if not os.path.exists(SERVICE_ACCOUNT_FILE):
    raise ValueError(f"❌ Service account file not found: {SERVICE_ACCOUNT_FILE}")
try:
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    sheets_service = build("sheets", "v4", credentials=creds)
except Exception as e:
    raise ValueError(f"❌ Failed to load Google Sheets credentials: {e}")

# ---------------- WEBHOOK SETUP ---------------- #
@app.route("/setWebhook", methods=["GET"])
def set_webhook():
    """Manually set the Telegram webhook if needed."""
    webhook_url = f"https://your-server.com/{BOT_TOKEN}"
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
    response = requests.post(url, json={"url": webhook_url})
    return response.json()

@app.route("/")
def index():
    return f"✅ Crypto Tracker Bot is running! Webhook: /{BOT_TOKEN}"

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    """Handles incoming Telegram messages."""
    try:
        update = request.get_json()
        print(f"📩 Received update: {update}")

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
            send_telegram_message(chat_id, "❌ Unknown command. Use /start, /add, /average, or /holdings.")

        return "ok", 200
    except Exception as e:
        traceback.print_exc()
        print(f"❌ Error in telegram_webhook: {e}")
        return "error", 500

# ---------------- GOOGLE SHEETS HELPER FUNCTIONS ---------------- #
def create_sheet_if_not_exists(sheet_name):
    """Check if a sheet exists, and create it if not."""
    try:
        spreadsheet = sheets_service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
        sheet_titles = [sheet["properties"]["title"] for sheet in spreadsheet.get("sheets", [])]

        if sheet_name not in sheet_titles:
            sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=SPREADSHEET_ID,
                body={"requests": [{"addSheet": {"properties": {"title": sheet_name}}}]}
            ).execute()
    except Exception as e:
        print(f"⚠️ Error checking/creating sheet {sheet_name}: {e}")

def append_to_sheet(sheet_name, values):
    """Append a row of data to the given Google Sheet."""
    try:
        sheets_service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{sheet_name}!A:A",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [values]}
        ).execute()
    except Exception as e:
        print(f"⚠️ Error appending to sheet {sheet_name}: {e}")

# ---------------- DATA PROCESSING FUNCTIONS ---------------- #
def calculate_total_holdings_for_person_and_coin(person, coin):
    """Calculate total holdings of a specific person for a given coin."""
    try:
        result = sheets_service.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range=f"{person}!A2:H").execute()
        data = result.get("values", [])

        total_quantity = sum(
            float(row[4]) * (1 if row[7].strip().upper() == "BUY" else -1)
            for row in data if len(row) >= 8 and row[2].upper() == coin
        )

        return f"📊 {person.capitalize()}'s total holdings for {coin}: {total_quantity:.4f}"
    except Exception as e:
        traceback.print_exc()
        return f"❌ Error calculating holdings for {person} and {coin}: {e}"

def calculate_total_holdings_for_coin(coin):
    """Calculate total holdings for a specific coin."""
    try:
        result = sheets_service.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range=f"{coin}!A2:H").execute()
        data = result.get("values", [])

        total_quantity = sum(
            float(row[4]) * (1 if row[7].strip().upper() == "BUY" else -1)
            for row in data if len(row) >= 8
        )

        return f"📊 Total holdings for {coin}: {total_quantity:.4f}"
    except Exception as e:
        traceback.print_exc()
        return f"❌ Error calculating holdings for {coin}: {e}"

def calculate_average(coin):
    """Calculate the average buy price for a coin."""
    try:
        result = sheets_service.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range=f"{coin}!A2:H").execute()
        data = result.get("values", [])

        buy_entries = [row for row in data if len(row) >= 8 and row[7].strip().upper() == "BUY"]
        total_quantity = sum(float(row[4]) for row in buy_entries)
        total_cost = sum(float(row[6]) for row in buy_entries)

        if total_quantity == 0:
            return f"📊 No valid buy entries for {coin}."

        average_price = total_cost / total_quantity
        return f"📊 Average price for {coin}: ${average_price:.2f} (Total held: {total_quantity})"
    except Exception as e:
        traceback.print_exc()
        return f"❌ Error calculating average for {coin}: {e}"

# ---------------- TELEGRAM API FUNCTIONS ---------------- #
def send_telegram_message(chat_id, text):
    """Send a message back to the user via Telegram, handling long messages."""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        for chunk in [text[i:i+4096] for i in range(0, len(text), 4096)]:
            requests.post(url, json={"chat_id": chat_id, "text": chunk})
    except Exception as e:
        print(f"⚠️ Failed to send message: {e}")

# ---------------- RUN FLASK APP ---------------- #
if __name__ == "__main__":
    print(f"✅ Bot is running! Webhook listening at: /{BOT_TOKEN}")
    app.run(host="0.0.0.0", port=5000, debug=True)
