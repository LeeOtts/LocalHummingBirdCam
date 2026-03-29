#!/usr/bin/env python3
"""
TikTok OAuth Setup Helper for Backyard Hummers

Walks you through the TikTok OAuth 2.0 flow to get access + refresh tokens.

Usage:
    python scripts/tiktok_auth.py

You'll need:
    1. Client Key (from TikTok Developer Portal)
    2. Client Secret (from TikTok Developer Portal)

Prerequisites:
    - Register at https://developers.tiktok.com
    - Create an app with "Content Posting API" scope
    - Add redirect URI: http://localhost:8585/callback
    - App must request 'video.publish' scope (and optionally 'video.upload')

This script will:
    - Open a browser for TikTok login authorization
    - Run a local server to catch the OAuth callback
    - Exchange the auth code for access + refresh tokens
    - Save tokens to .env and .tiktok_tokens.json

Note: Unaudited TikTok apps can only post to private mode. Once your
integration is tested, submit your app for audit at developers.tiktok.com.
"""

import json
import os
import sys
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs

import requests
from dotenv import load_dotenv

load_dotenv()

TIKTOK_AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
REDIRECT_URI = "http://localhost:8585/callback"
SCOPES = "video.publish,video.upload"

# Will be set by the callback handler
_auth_code = None
_server = None


class CallbackHandler(BaseHTTPRequestHandler):
    """Handle the OAuth callback from TikTok."""

    def do_GET(self):
        global _auth_code
        parsed = urlparse(self.path)

        if parsed.path == "/callback":
            params = parse_qs(parsed.query)

            if "code" in params:
                _auth_code = params["code"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<html><body><h2>Authorization successful!</h2>"
                    b"<p>You can close this tab and return to the terminal.</p>"
                    b"</body></html>"
                )
            elif "error" in params:
                error = params.get("error", ["unknown"])[0]
                error_desc = params.get("error_description", [""])[0]
                self.send_response(400)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    f"<html><body><h2>Authorization failed</h2>"
                    f"<p>Error: {error}</p><p>{error_desc}</p>"
                    f"</body></html>".encode()
                )
            else:
                self.send_response(400)
                self.end_headers()

            # Shut down the server after handling
            threading.Thread(target=_server.shutdown).start()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Suppress request logs
        pass


def start_auth_flow(client_key: str) -> str:
    """Open browser for TikTok authorization, return auth code."""
    global _server

    params = {
        "client_key": client_key,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "response_type": "code",
    }

    auth_url = f"{TIKTOK_AUTH_URL}?{urlencode(params)}"

    print("\n[1/3] Opening browser for TikTok authorization...")
    print(f"  If the browser doesn't open, visit this URL manually:\n")
    print(f"  {auth_url}\n")

    _server = HTTPServer(("localhost", 8585), CallbackHandler)

    # Open browser
    webbrowser.open(auth_url)

    print("  Waiting for authorization callback...")
    _server.serve_forever()

    if not _auth_code:
        print("ERROR: No authorization code received.")
        sys.exit(1)

    print("  Authorization code received!")
    return _auth_code


def exchange_code(client_key: str, client_secret: str, code: str) -> dict:
    """Exchange authorization code for access + refresh tokens."""
    print("\n[2/3] Exchanging code for tokens...")

    resp = requests.post(
        TIKTOK_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
        },
        timeout=30,
    )

    if resp.status_code != 200:
        print(f"ERROR: Token exchange failed: {resp.text}")
        sys.exit(1)

    data = resp.json()

    if "access_token" not in data:
        print(f"ERROR: No access_token in response: {data}")
        sys.exit(1)

    print("  Got access and refresh tokens!")
    print(f"  Token expires in: {data.get('expires_in', 'unknown')} seconds")
    print(f"  Refresh token expires in: {data.get('refresh_expires_in', 'unknown')} seconds")

    return data


def save_tokens(client_key: str, client_secret: str, token_data: dict):
    """Save tokens to .env and .tiktok_tokens.json."""
    print("\n[3/3] Saving tokens...")

    project_root = Path(__file__).parent.parent
    env_path = project_root / ".env"
    token_path = project_root / ".tiktok_tokens.json"

    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token", "")

    # Save to .tiktok_tokens.json (used by TikTokPoster for auto-refresh)
    token_path.write_text(json.dumps({
        "access_token": access_token,
        "refresh_token": refresh_token,
    }, indent=2))
    print(f"  Saved tokens to {token_path}")

    # Update .env
    if env_path.exists():
        content = env_path.read_text()
    else:
        example_path = project_root / ".env.example"
        content = example_path.read_text() if example_path.exists() else ""

    env_vars = {
        "TIKTOK_CLIENT_KEY": client_key,
        "TIKTOK_CLIENT_SECRET": client_secret,
        "TIKTOK_ACCESS_TOKEN": access_token,
        "TIKTOK_REFRESH_TOKEN": refresh_token,
    }

    lines = content.split("\n")
    updated = set()

    for i, line in enumerate(lines):
        for key, value in env_vars.items():
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={value}"
                updated.add(key)

    for key, value in env_vars.items():
        if key not in updated:
            lines.append(f"{key}={value}")

    env_path.write_text("\n".join(lines))
    print(f"  Updated {env_path}")


def verify_token(access_token: str):
    """Quick check that the token works."""
    print("\n[Verify] Testing token...")
    resp = requests.get(
        "https://open.tiktokapis.com/v2/user/info/",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"fields": "display_name,avatar_url"},
        timeout=30,
    )

    if resp.status_code == 200:
        data = resp.json().get("data", {}).get("user", {})
        name = data.get("display_name", "unknown")
        print(f"  Authenticated as: {name}")
        print("  Token is VALID!")
    else:
        print(f"  WARNING: Token verification returned {resp.status_code}: {resp.text}")
        print("  (This may be fine if your app doesn't have user.info.basic scope)")


def main():
    print("=" * 50)
    print("  Backyard Hummers - TikTok OAuth Setup")
    print("=" * 50)
    print()
    print("Prerequisites:")
    print("  1. Register at https://developers.tiktok.com")
    print("  2. Create an app with 'Content Posting API' scope")
    print("  3. Add redirect URI: http://localhost:8585/callback")
    print()

    # Check if values already in env
    client_key = os.getenv("TIKTOK_CLIENT_KEY", "")
    client_secret = os.getenv("TIKTOK_CLIENT_SECRET", "")

    if client_key:
        print(f"  Found TIKTOK_CLIENT_KEY in .env: {client_key[:8]}...")
        use_existing = input("  Use this? [Y/n]: ").strip().lower()
        if use_existing == "n":
            client_key = ""

    if not client_key:
        client_key = input("  Client Key: ").strip()
    if not client_secret:
        client_secret = input("  Client Secret: ").strip()

    if not client_key or not client_secret:
        print("ERROR: Both Client Key and Client Secret are required.")
        sys.exit(1)

    # Step 1: Browser authorization
    code = start_auth_flow(client_key)

    # Step 2: Exchange code for tokens
    token_data = exchange_code(client_key, client_secret, code)

    # Step 3: Save everything
    save_tokens(client_key, client_secret, token_data)

    # Verify
    verify_token(token_data["access_token"])

    print()
    print("=" * 50)
    print("  Setup complete! TikTok is ready.")
    print()
    print("  Tokens auto-refresh when they expire.")
    print()
    print("  IMPORTANT: Until your app passes TikTok's audit,")
    print("  posts will only be visible in private mode.")
    print("  Submit for audit at developers.tiktok.com")
    print("=" * 50)


if __name__ == "__main__":
    main()
