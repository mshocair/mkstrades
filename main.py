import os
import base64
import json

from flask import Flask, request
from google.oauth2 import service_account
from googleapiclient.discovery import build

app = Flask(__name__)

# 1) Read environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "")
creds_b64 = os.getenv("GOOGLE_CREDS_B64", "")

# 2) Decode the base64 service account JSON
if not creds_b64:
    raise ValueError("GOOGLE_CREDS_B64 is not set in environment.")
try:
    service_account_info = json.loads(base64.b64decode(creds_b64))
except Exception as e:
    raise ValueError("Failed to decode service account JSON: " + str(e))

# 3) Create the Google Sheets API client
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
creds = service_account.Credentials.from_service_account_info(
    service_account_info, scopes=SCOPES
)
sheets_service = build("sheets", "v4", credentials=creds)


@app.route("/")
def index():
    return "Hello from Render + Python + Google Sheets!"

@app.route("/test-append")
def test_append():
    """Example route to add a row to the Google Sheet"""
    try:
        row_values = [
            ["Hello", "Render!", "It works."]
        ]

        body = {"values": row_values}
        sheets_service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range="Sheet1!A1",   # or "Trades!A1"
            valueInputOption="USER_ENTERED",
            body=body
        ).execute()

        return "Row added successfully!"
    except Exception as e:
        return "Error: " + str(e)

# If you have a Telegram webhook, define it similarly:
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    # parse Telegram update and respond
    return "ok"

if __name__ == "__main__":
    # This is for local testing. On Render, we use gunicorn main:app
    app.run(host="0.0.0.0", port=5000, debug=True)
