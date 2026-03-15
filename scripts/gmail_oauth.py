"""One-time OAuth setup for Gmail MCP.

Uses Google's installed app (Desktop) flow — opens browser, user consents,
token is saved to ~/.gmail-mcp/credentials.json for the MCP server to use.
"""

import json
from pathlib import Path

GMAIL_MCP_DIR = Path.home() / ".gmail-mcp"
OAUTH_KEYS = GMAIL_MCP_DIR / "gcp-oauth.keys.json"
CREDENTIALS = GMAIL_MCP_DIR / "credentials.json"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.settings.basic",
]


def main():
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Installing google-auth-oauthlib...")
        import subprocess, sys
        subprocess.check_call([sys.executable, "-m", "pip", "install",
                               "google-auth-oauthlib"])
        from google_auth_oauthlib.flow import InstalledAppFlow

    if not OAUTH_KEYS.exists():
        print(f"ERROR: {OAUTH_KEYS} not found")
        return

    flow = InstalledAppFlow.from_client_secrets_file(str(OAUTH_KEYS), SCOPES)
    creds = flow.run_local_server(port=0)  # picks any free port

    # Save in the format the MCP server expects
    token_data = {
        "access_token": creds.token,
        "refresh_token": creds.refresh_token,
        "scope": " ".join(SCOPES),
        "token_type": "Bearer",
        "expiry_date": int(creds.expiry.timestamp() * 1000) if creds.expiry else None,
    }

    CREDENTIALS.write_text(json.dumps(token_data, indent=2))
    print(f"\nToken saved to {CREDENTIALS}")
    print("Gmail MCP is now authorized. Run: applypilot track --setup")


if __name__ == "__main__":
    main()
