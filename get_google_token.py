import os
import json
from google_auth_oauthlib.flow import InstalledAppFlow

# Complete master scopes for ARGUS
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/drive.readonly"  # Permanent Drive scope
]

def generate_tokens():
    flow = InstalledAppFlow.from_client_secrets_file(
        "credentials.json", # or client_secret.json
        scopes=SCOPES
    )
    
    # CRITICAL: prompt='consent' forces Google to mint a fresh Refresh Token
    # access_type='offline' allows ARGUS to refresh tokens autonomously
    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent"
    )

    print("\n" + "="*50)
    print("PERMANENT OAUTH CREDENTIALS FOR RENDER")
    print("="*50)
    print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")
    print(f"ACCESS_TOKEN={creds.token}")
    print("="*50 + "\n")

if __name__ == "__main__":
    generate_tokens()

def main():
    # Uses your credentials JSON downloaded from Google Cloud Console
    client_secrets_file = "client_secret.json"
    
    if not os.path.exists(client_secrets_file):
        print(f"Error: Place your '{client_secrets_file}' in the current folder.")
        return

    flow = InstalledAppFlow.from_client_secrets_file(
        client_secrets_file, 
        scopes=SCOPES
    )
    # prompt='consent' and access_type='offline' are required to force a refresh token
    creds = flow.run_local_server(
        port=8080, 
        prompt="consent", 
        access_type="offline"
    )

    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes
    }

    print("\n" + "="*50)
    print("SUCCESS! Copy the values below for Render:")
    print("="*50)
    print(f"ACCESS_TOKEN: {creds.token}")
    print(f"GOOGLE_REFRESH_TOKEN: {creds.refresh_token}")
    print("\nOR FULL JSON (for GOOGLE_CALENDAR_TOKEN):")
    print(json.dumps(token_data))
    print("="*50)

if __name__ == "__main__":
    main()