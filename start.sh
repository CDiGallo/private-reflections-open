#!/bin/bash
set -e

# ── Read just the few values we need from .env, safely ──────────────────────
# (Sourcing .env directly breaks on values with spaces, e.g. SAVE_KEYWORD=wrap up,
#  so we grep only the flags that control which profiles to start.)
get_env() {
    grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d "\"' " || true
}

LLM_PROVIDER=$(get_env LLM_PROVIDER)
TELEGRAM_ENABLED=$(get_env TELEGRAM_ENABLED)

# ── Pick the LLM backend profile ────────────────────────────────────────────
if [ "$LLM_PROVIDER" = "huggingface" ]; then
    echo "🤖 Using HuggingFace / vLLM backend..."
    PROFILE_FLAGS="--profile huggingface"
else
    echo "🦙 Using Ollama backend..."
    PROFILE_FLAGS="--profile ollama"
fi

# ── Add Telegram profile if enabled ─────────────────────────────────────────
if [ "${TELEGRAM_ENABLED:-false}" = "true" ]; then
    echo "📱 Telegram bot enabled..."
    PROFILE_FLAGS="$PROFILE_FLAGS --profile telegram"
fi

echo "▶  docker compose $PROFILE_FLAGS up --build -d"
docker compose $PROFILE_FLAGS up --build -d

echo "⏳ Waiting for diary-app to be ready..."
until [ "$(docker inspect -f '{{.State.Running}}' diary-app 2>/dev/null)" = "true" ]; do
    sleep 1
done

echo "✅ Attaching. Press Ctrl+P then Ctrl+Q to detach without stopping."
docker attach diary-app
