import os
import json
import traceback  # For detailed error tracebacks
from flask import Flask, request
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)

# 1) Read environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "")
SERVICE_ACCOUNT_FILE = os.getenv("SERVICE_ACCOUNT_FILE", "service_account.json")  # Path to JSON file

# 2) Load the service account JSON directly from the file
if not os.path.exists(SERVICE_ACCOUNT_FILE):
    raise ValueError(f"Service account file not found: {SERVICE_ACCOUNT_FILE}")
try:
    # Load the credentials from the file
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    # Initialize the Google Sheets API client
    sheets_service = build("sheets", "v4", credentials=creds)
except Exception as e:
    raise ValueError(f"Failed to load service account credentials: {e}")

@app.route("/")
def index():
    return "Hello from Render + Python + Google Sheets!"

@app.route("/test-append")
def test_append():
    """Example route to add a row to the Google Sheet"""
    try:
        # Example row to append
        row_values = [
            ["Hello", "Render!", "It works."]
        ]

        body = {"values": row_values}
        sheets_service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range="Trades!A1",   # or "Trades!A1"
            valueInputOption="USER_ENTERED",
            body=body
        ).execute()

        return "Row added successfully!"
    except Exception as e:
        # Log the full stack trace for debugging
        traceback.print_exc()

        # Log the error to the console
        print(f"Error in /test-append: {e}")

        # Return a detailed error message
        return f"Error: {e}", 500

# If you have a Telegram webhook, define it similarly:
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    # parse Telegram update and respond
    return "ok"

if __name__ == "__main__":
    # This is for local testing. On Render, we use gunicorn main:app
    app.run(host="0.0.0.0", port=5000, debug=True)
