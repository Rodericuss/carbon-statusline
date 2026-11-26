#!/bin/sh
# Instala a Carbon statusline: ccstatusline fixado, config e statusLine do Claude Code.
set -eu
DIR=$(cd "$(dirname "$0")" && pwd)
CCS_VERSION=2.2.34
npm i -g --prefix "$HOME/.local" "ccstatusline@$CCS_VERSION"
chmod +x "$DIR/carbon.py"
mkdir -p "$HOME/.config/ccstatusline"
if [ -f "$HOME/.config/ccstatusline/settings.json" ]; then
  cp "$HOME/.config/ccstatusline/settings.json" "$HOME/.config/ccstatusline/settings.json.bak-$(date +%Y%m%d%H%M%S)"
fi
sed "s#__CARBON__#$DIR/carbon.py#g" "$DIR/ccstatusline/settings.template.json" > "$HOME/.config/ccstatusline/settings.json"
SETTINGS="$HOME/.claude/settings.json"
mkdir -p "$HOME/.claude"
[ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"
cp "$SETTINGS" "$SETTINGS.bak-statusline-$(date +%Y%m%d%H%M%S)"
tmp=$(mktemp)
jq --arg c "$HOME/.local/bin/ccstatusline" '.statusLine = {type: "command", command: $c, padding: 0}' "$SETTINGS" > "$tmp" && mv "$tmp" "$SETTINGS"
python3 -m unittest discover -s "$DIR/tests" -q
echo "Pronto. Para o modo rápido sem ccstatusline: statusLine.command = $DIR/carbon.py render"
