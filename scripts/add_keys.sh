#!/bin/zsh
# Prompts for API keys (input hidden) and appends them to .env. Leave blank to skip one.
cd "$(dirname "$0")/.." || exit 1
touch .env && chmod 600 .env
for name in HUBSPOT_TOKEN SLACK_WEBHOOK_URL HUNTER_API_KEY; do
  read -rs "val?$name (Enter to skip): "
  echo
  if [[ -n "$val" ]]; then
    grep -v "^$name=" .env > .env.tmp; mv .env.tmp .env
    print -r -- "$name=$val" >> .env
    echo "  saved $name"
  fi
  unset val
done
chmod 600 .env
echo "done: $(cut -d= -f1 .env | tr '\n' ' ')"
