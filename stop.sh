#!/bin/bash
set -e

# Stop every service across all profiles. The external `ollama` volume and the
# session files on your Windows drive survive this.
docker compose \
    --profile ollama \
    --profile huggingface \
    --profile telegram \
    down

echo "🛑 All diary services stopped."
