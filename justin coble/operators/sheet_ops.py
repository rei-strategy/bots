import os
import sys
import time
import subprocess
from google.auth.exceptions import RefreshError
from google.oauth2 import service_account
from googleapiclient.discovery import build
import google.auth

SPREADSHEET_ID = "1CWR7fGEIekNNuVodzg2kjxn3_AvoKBVfrpFCydDKCv8"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/cloud-platform",
]

def _ensure_authenticated():
    """
    On RefreshError, invoke gcloud to reauthenticate.
    """
    print("🔑 Attempting to reauthenticate with gcloud…")
    cmd = [
        "gcloud", "auth", "application-default", "login",
        f"--scopes={','.join(SCOPES)}"
    ]
    try:
        subprocess.run(cmd, check=True)
        print("✅ gcloud reauthentication succeeded.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ gcloud reauthentication failed: {e}")
        return False

def _build_service():
    """
    Build a Sheets service client, preferring a service‐account key if provided,
    otherwise falling back to ADC.
    """
    key_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if key_path:
        if not os.path.isfile(key_path):
            print(f"⚠️ Service account key not found at {key_path}")
            sys.exit(1)
        creds = service_account.Credentials.from_service_account_file(
            key_path, scopes=SCOPES
        )
        print(f"🔒 Using service account credentials from {key_path}")
    else:
        creds, _ = google.auth.default(scopes=SCOPES)
        print("🔒 Using Application Default Credentials")
    return build("sheets", "v4", credentials=creds)

# Initialize the Sheets client
_service = _build_service()

def appendToSheet(record: dict) -> bool:
    global _service
    """
    Appends one row to Sheet1!A:L with:
      [Sale Date, First Name, Last Name, File #, Property, City, Zip, County, Bid, Equity, Source, Error]
    """
    row = [
        record.get("saleDate", ""),
        record.get("firstName", ""),
        record.get("lastName", ""),
        record.get("fileNumber", ""),
        record.get("property", {}).get("address", ""),
        record.get("city", ""),
        record.get("zip", ""),
        record.get("county", ""),
        record.get("bid", ""),
        record.get("estValue", ""),
        record.get("source", ""),
        record.get("error", ""),
    ]

    for attempt in range(2):
        try:
            _service.spreadsheets().values().append(
                spreadsheetId=SPREADSHEET_ID,
                range="Sheet1!A:L",
                valueInputOption="RAW",
                body={"values": [row]},
            ).execute()
            time.sleep(1.1)  # throttle to respect write‐rate quota
            return True

        except RefreshError as e:
            print("⚠️ Sheets auth expired on append:", e)
            if attempt == 0 and _ensure_authenticated():
                _service = _build_service()
                print("🔄 Retrying append…")
                continue
            print("🚨 Could not append after reauth. Exiting.")
            sys.exit(1)

def getLastProcessedFileNumberBySource(source: str) -> str | None:
    global _service
    """
    Reads Sheet1!D2:K and returns the last File # (col D) where column K == source.
    """
    for attempt in range(2):
        try:
            result = _service.spreadsheets().values().get(
                spreadsheetId=SPREADSHEET_ID,
                range="Sheet1!D2:K",
            ).execute()
            values = result.get("values", [])
            # col 0 is File #, col 7 is Source
            filtered = [
                row[0]
                for row in values
                if len(row) >= 8 and row[7] == source
            ]
            return filtered[-1] if filtered else None

        except RefreshError as e:
            print("⚠️ Sheets auth expired on fetch:", e)
            if attempt == 0 and _ensure_authenticated():
                _service = _build_service()
                print("🔄 Retrying fetch…")
                continue
            print("🚨 Could not fetch after reauth. Exiting.")
            sys.exit(1)

def getSourcesInSheet() -> list[str]:
    """
    Reads column K (Source) from the sheet and returns
    the unique list of non-empty source keys found.
    """
    global _service
    for attempt in range(2):
        try:
            result = _service.spreadsheets().values().get(
                spreadsheetId=SPREADSHEET_ID,
                range="Sheet1!K2:K",
            ).execute()
            values = result.get("values", [])
            # flatten, strip, dedupe
            sources = sorted({row[0].strip() for row in values if row and row[0].strip()})
            return sources
        except RefreshError as e:
            print("⚠️ Sheets auth expired on fetch in getSourcesInSheet:", e)
            if attempt == 0 and _ensure_authenticated():
                _service = _build_service()
                print("🔄 Retrying getSourcesInSheet…")
                continue
            print("🚨 Could not fetch sources after reauth. Exiting.")
            sys.exit(1)
    return []