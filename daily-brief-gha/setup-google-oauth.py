#!/usr/bin/env python3
"""
One-time Google OAuth setup for Daily Brief GitHub Actions.
Run this locally once to get your refresh token, then store it in GitHub secrets.

Requirements:
  pip install google-auth-oauthlib google-auth-httplib2

Usage:
  python3 setup-google-oauth.py
"""

import json, os
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]

print("""
=== Google OAuth Setup for Daily Brief ===

Before running this, you need a Google Cloud OAuth client:
1. Go to https://console.cloud.google.com/
2. Create a project (or use existing)
3. Enable: Gmail API + Google Calendar API
4. Go to APIs & Services → Credentials → Create OAuth 2.0 Client ID
5. Application type: Desktop app
6. Download the JSON → save as 'client_secret.json' in this folder

Then press Enter to continue...
""")
input()

if not os.path.exists("client_secret.json"):
    print("❌ client_secret.json not found. Download it from Google Cloud Console first.")
    exit(1)

flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
creds = flow.run_local_server(port=0)

with open("client_secret.json") as f:
    client_data = json.load(f)

client_info = client_data.get("installed") or client_data.get("web", {})

print("\n✅ Auth complete! Add these as GitHub Actions secrets:\n")
print(f"GOOGLE_CLIENT_ID     = {client_info.get('client_id')}")
print(f"GOOGLE_CLIENT_SECRET = {client_info.get('client_secret')}")
print(f"GOOGLE_REFRESH_TOKEN = {creds.refresh_token}")
print("\nAlso add:")
print("ANTHROPIC_API_KEY    = sk-ant-...")
print("(EmailJS credentials are already hardcoded in the workflow)")
