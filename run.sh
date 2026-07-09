#!/bin/zsh
# loopkit launcher — loads Slack tokens from ~/.loopkit.env then starts the bot.
# Usage: ./run.sh
set -e
ENV_FILE="$HOME/.loopkit.env"
if [ ! -f "$ENV_FILE" ]; then
  echo "❌ missing $ENV_FILE (needs: export SLACK_BOT_TOKEN=... / export SLACK_APP_TOKEN=...)"
  exit 1
fi
source "$ENV_FILE"
cd "$(dirname "$0")"
PYTHONPATH="$(dirname "$0")/src" exec python -m loopkit.fronts.slack
