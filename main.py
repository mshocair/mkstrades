from google.oauth2 import service_account
from googleapiclient.discovery import build

# Point to your downloaded JSON key file
SERVICE_ACCOUNT_FILE = "bold-forest-1234567abcde.json"

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
creds = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, 
    scopes=SCOPES
)

service = build("sheets", "v4", credentials=creds)

SPREADSHEET_ID = "1GKqvVz3vK5eo1UHyCT23rAXM4RSnVtf4FBmfWsbaeU8"  # from your sheet URL

# Example: Append a row
values = [
    ["Hello", "World"]  # the columns
]
body = {
    "values": values
}

service.spreadsheets().values().append(
    spreadsheetId=SPREADSHEET_ID,
    range="Sheet1!A1",  # or "Trades!A1"
    valueInputOption="USER_ENTERED",
    body=body
).execute()

print("Row added!")
