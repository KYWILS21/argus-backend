"""
One-time setup script: run this ONCE locally to grant ARGUS permission to
read your Google Calendar. It opens a browser for you to log in and
approve access, then prints a "refresh token" -- a long-lived credential
that lets the backend check your calendar going forward without you
logging in again.

Usage:
    1. Place the credentials JSON file you downloaded from Google Cloud
       Console in this same folder, named exactly: client_secret.json
    2. Run: python get_refresh_token.py
    3. A browser window opens -- log in and click Allow.
    4. Copy the refresh token this script prints out.
    5. Set it as GOOGLE_REFRESH_TOKEN in your environment (locally and on
       Railway), alongside GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET
       (both of which are also in that same JSON file, printed below too).

You only need to run this once. Delete client_secret.json afterwards if
you like -- the refresh token is what matters going forward, not the file.
"""

import json

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def main():
    flow = InstalledAppFlow.from_client_secrets_file(
        "client_secret.json", SCOPES
    )
    creds = flow.run_local_server(port=0)

    with open("client_secret.json") as f:
        client_config = json.load(f)["installed"]

    print("\n--- Copy these into your environment variables ---\n")
    print(f"GOOGLE_CLIENT_ID={client_config['client_id']}")
    print(f"GOOGLE_CLIENT_SECRET={client_config['client_secret']}")
    print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")
    print("\n----------------------------------------------------")
    print(
        "\nSet these three in both your local terminal (for testing) and "
        "Railway's Variables tab (for the live deployment)."
    )


if __name__ == "__main__":
    main()