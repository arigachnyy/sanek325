"""
One-time interactive setup: generates token.json for headless Gmail API access.

Usage:
    python setup_oauth.py

Prerequisites:
    1. Go to https://console.cloud.google.com/
    2. Create a project (or select existing)
    3. Enable the Gmail API
    4. Create OAuth 2.0 credentials (Application type: Desktop app)
    5. Download the JSON and save it as credentials.json in this directory
"""

import os

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CREDENTIALS_PATH = os.path.join(os.path.dirname(__file__), "credentials.json")
TOKEN_PATH = os.path.join(os.path.dirname(__file__), "token.json")


def main():
    if not os.path.exists(CREDENTIALS_PATH):
        print(f"Error: {CREDENTIALS_PATH} not found.")
        print("Download your OAuth2 Desktop credentials from Google Cloud Console")
        print("and save the file as credentials.json in this directory.")
        raise SystemExit(1)

    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(TOKEN_PATH, "w") as f:
        f.write(creds.to_json())

    print(f"Token saved to {TOKEN_PATH}")
    print("You can now run: python main.py")


if __name__ == "__main__":
    main()
