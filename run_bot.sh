#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")"
export PYTHONUNBUFFERED=1
: "${SUPABASE_URL:?SUPABASE_URL is required}"
: "${SUPABASE_KEY:?SUPABASE_KEY is required}"
exec python3 bot.py
