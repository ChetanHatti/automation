"""
authenticate.py - Bulletproof YouTube OAuth authenticator.
"""

import os
import sys
import webbrowser
import logging
from google_auth_oauthlib.flow import InstalledAppFlow

logging.basicConfig(level=logging.INFO, format="%(message)s")

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
SECRETS_FILE = os.path.join(os.path.dirname(__file__), "client_secrets.json")
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "token.json")


def main():
    print("=" * 60)
    print("        YouTube OAuth Authentication Setup")
    print("=" * 60)

    if not os.path.exists(SECRETS_FILE):
        print(f"ERROR: Client secrets file not found: {SECRETS_FILE}")
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(
        SECRETS_FILE,
        scopes=SCOPES,
    )

    print("\nStarting local server on port 8080...")
    sys.stdout.flush()

    try:
        # run_local_server automatically sets redirect_uri to http://localhost:8080/
        # and opens the browser.
        creds = flow.run_local_server(
            host="localhost",
            port=8080,
            authorization_prompt_message=(
                "\nIf the browser did not open automatically, open this URL in your browser:\n\n{url}\n"
            ),
            success_message="Authentication successful! token.json is being saved. You may close this tab.",
            open_browser=True,
        )
    except Exception as exc:
        print(f"\n[!] Note on automatic listener: {exc}")
        print("\nIf your browser is showing 'This site can't be reached' or redirected to http://localhost:8080/?state=...")
        print("Copy the ENTIRE URL from your browser address bar and paste it below:\n")
        sys.stdout.flush()
        redirect_url = input("Paste URL here: ").strip()
        if not redirect_url:
            print("No URL provided. Exiting.")
            sys.exit(1)
        # OAuthlib requires https for parsing
        if redirect_url.startswith("http://"):
            redirect_url = "https://" + redirect_url[7:]
        flow.fetch_token(authorization_response=redirect_url)
        creds = flow.credentials

    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(creds.to_json())

    print("\n" + "=" * 60)
    print(f" SUCCESS! token.json saved to {TOKEN_FILE}")
    print("=" * 60)


if __name__ == "__main__":
    main()
