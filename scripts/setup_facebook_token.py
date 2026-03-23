#!/usr/bin/env python3
"""
Facebook Token Setup Helper for Backyard Hummers

Exchanges your short-lived access token for a permanent Page Access Token.

Usage:
    python scripts/setup_facebook_token.py

You'll need:
    1. App ID (from Meta Developer dashboard)
    2. App Secret (from Meta Developer dashboard → Settings → Basic)
    3. Short-lived User Access Token (from Graph API Explorer with pages_manage_posts,
       pages_read_engagement, pages_read_user_content permissions)

This script will:
    - Exchange the short-lived token for a long-lived token (60 days)
    - List your Facebook pages so you can pick "Backyard Hummers"
    - Get the permanent Page Access Token
    - Write everything to your .env file
"""

import sys
from pathlib import Path

import requests


GRAPH_API = "https://graph.facebook.com/v22.0"


def get_long_lived_token(app_id: str, app_secret: str, short_token: str) -> str:
    """Exchange a short-lived user token for a long-lived one (60 days)."""
    print("\n[1/3] Exchanging for long-lived token...")
    resp = requests.get(
        f"{GRAPH_API}/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": short_token,
        },
        timeout=30,
    )

    if resp.status_code != 200:
        print(f"ERROR: {resp.json().get('error', {}).get('message', resp.text)}")
        sys.exit(1)

    token = resp.json()["access_token"]
    print("  Got long-lived user token!")
    return token


def get_pages(long_lived_token: str) -> list:
    """List all pages the user manages."""
    print("\n[2/3] Fetching your Facebook pages...")
    resp = requests.get(
        f"{GRAPH_API}/me/accounts",
        params={"access_token": long_lived_token},
        timeout=30,
    )

    if resp.status_code != 200:
        print(f"ERROR: {resp.json().get('error', {}).get('message', resp.text)}")
        sys.exit(1)

    pages = resp.json().get("data", [])
    if not pages:
        print("ERROR: No pages found. Make sure your token has pages_manage_posts permission.")
        sys.exit(1)

    return pages


def update_env_file(page_id: str, page_token: str):
    """Write the page credentials to .env."""
    env_path = Path(__file__).parent.parent / ".env"

    # Read existing .env or create from example
    if env_path.exists():
        content = env_path.read_text()
    else:
        example_path = Path(__file__).parent.parent / ".env.example"
        content = example_path.read_text() if example_path.exists() else ""

    # Replace or append values
    lines = content.split("\n")
    updated = {"FACEBOOK_PAGE_ID": False, "FACEBOOK_PAGE_ACCESS_TOKEN": False}

    for i, line in enumerate(lines):
        if line.startswith("FACEBOOK_PAGE_ID="):
            lines[i] = f"FACEBOOK_PAGE_ID={page_id}"
            updated["FACEBOOK_PAGE_ID"] = True
        elif line.startswith("FACEBOOK_PAGE_ACCESS_TOKEN="):
            lines[i] = f"FACEBOOK_PAGE_ACCESS_TOKEN={page_token}"
            updated["FACEBOOK_PAGE_ACCESS_TOKEN"] = True

    if not updated["FACEBOOK_PAGE_ID"]:
        lines.append(f"FACEBOOK_PAGE_ID={page_id}")
    if not updated["FACEBOOK_PAGE_ACCESS_TOKEN"]:
        lines.append(f"FACEBOOK_PAGE_ACCESS_TOKEN={page_token}")

    env_path.write_text("\n".join(lines))
    print(f"\n  Saved to {env_path}")


def verify_token(page_id: str, page_token: str):
    """Verify the token works by checking page info."""
    print("\n[Verify] Testing the token...")
    resp = requests.get(
        f"{GRAPH_API}/{page_id}",
        params={
            "fields": "name,id,access_token",
            "access_token": page_token,
        },
        timeout=30,
    )

    if resp.status_code == 200:
        data = resp.json()
        print(f"  Page name: {data.get('name')}")
        print(f"  Page ID:   {data.get('id')}")
        print("  Token is VALID!")
    else:
        print(f"  WARNING: Token verification failed: {resp.text}")


def main():
    print("=" * 50)
    print("  Backyard Hummers - Facebook Token Setup")
    print("=" * 50)
    print()
    print("You'll need these from https://developers.facebook.com:")
    print()

    app_id = input("  App ID: ").strip()
    app_secret = input("  App Secret: ").strip()
    print()
    print("Get a short-lived User Access Token from the Graph API Explorer:")
    print("  https://developers.facebook.com/tools/explorer/")
    print("  Required permissions: pages_manage_posts, pages_read_engagement")
    print()
    short_token = input("  Short-lived User Access Token: ").strip()

    if not all([app_id, app_secret, short_token]):
        print("ERROR: All three values are required.")
        sys.exit(1)

    # Step 1: Get long-lived token
    long_token = get_long_lived_token(app_id, app_secret, short_token)

    # Step 2: List pages and let user pick
    pages = get_pages(long_token)

    print(f"\n  Found {len(pages)} page(s):\n")
    for i, page in enumerate(pages):
        print(f"    [{i + 1}] {page['name']} (ID: {page['id']})")

    if len(pages) == 1:
        choice = 0
        print(f"\n  Auto-selected: {pages[0]['name']}")
    else:
        choice = int(input("\n  Enter page number: ").strip()) - 1
        if choice < 0 or choice >= len(pages):
            print("ERROR: Invalid selection.")
            sys.exit(1)

    selected_page = pages[choice]
    page_id = selected_page["id"]
    page_token = selected_page["access_token"]

    print(f"\n[3/3] Got permanent Page Access Token for '{selected_page['name']}'!")

    # Save to .env
    update_env_file(page_id, page_token)

    # Verify
    verify_token(page_id, page_token)

    print()
    print("=" * 50)
    print("  Setup complete! Your .env is ready.")
    print("  The page token does not expire.")
    print("=" * 50)


if __name__ == "__main__":
    main()
