#!/bin/bash
# Fix git merge conflicts with retry_queue.json
# This script handles the case where retry_queue.json exists locally but is being removed from tracking

set -e

cd "$(dirname "$0")" || exit 1

echo "[*] Fixing git merge conflicts..."

# If retry_queue.json exists locally, remove it (it will be recreated at runtime)
if [ -f retry_queue.json ]; then
    echo "[*] Removing local retry_queue.json (will be auto-created at runtime)"
    rm -f retry_queue.json
fi

# Make sure .gitignore is properly set
if ! grep -q "retry_queue.json" .gitignore; then
    echo "[*] Adding retry_queue.json to .gitignore"
    echo "retry_queue.json" >> .gitignore
    git add .gitignore
fi

# Ensure git knows to ignore this file even if it was tracked before
git update-index --assume-unchanged retry_queue.json 2>/dev/null || true

# Clean up any merge state
if [ -d .git/MERGE_HEAD ]; then
    echo "[*] Aborting incomplete merge..."
    git merge --abort 2>/dev/null || true
fi

echo "[✓] Git merge conflicts resolved"
echo "[*] Safe to run: git pull origin main"
