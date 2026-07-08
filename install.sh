#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${1:-https://github.com/knee5/signed-agent-receipts.git}"
PROFILE="${HERMES_PROFILE:-default}"
WORKDIR="${AGENT_RECEIPTS_INSTALL_DIR:-$HOME/.cache/signed-agent-receipts/install}"
if [ -n "${PYTHON:-}" ]; then
  PYTHON_BIN="$PYTHON"
elif command -v python3.12 >/dev/null 2>&1; then
  PYTHON_BIN="python3.12"
elif command -v python3.11 >/dev/null 2>&1; then
  PYTHON_BIN="python3.11"
elif command -v python3.10 >/dev/null 2>&1; then
  PYTHON_BIN="python3.10"
else
  PYTHON_BIN="python3"
fi

mkdir -p "$(dirname "$WORKDIR")"
if [ -d "$WORKDIR/.git" ]; then
  git -C "$WORKDIR" fetch --depth 1 origin main
  git -C "$WORKDIR" checkout -q origin/main
else
  if [ -e "$WORKDIR" ]; then
    echo "Install directory exists but is not a git checkout: $WORKDIR" >&2
    echo "Set AGENT_RECEIPTS_INSTALL_DIR to an empty path or remove it manually." >&2
    exit 2
  fi
  git clone --depth 1 "$REPO_URL" "$WORKDIR"
fi

if "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)' >/dev/null 2>&1; then
  "$PYTHON_BIN" -m pip install "$WORKDIR"
else
  "$PYTHON_BIN" -m pip install --user "$WORKDIR" || "$PYTHON_BIN" -m pip install --user --break-system-packages "$WORKDIR"
fi

if command -v hermes >/dev/null 2>&1; then
  HERMES_HOME_BASE="${HERMES_HOME:-$HOME/.hermes}"
  if [ "$PROFILE" = "default" ]; then
    PROFILE_HOME="$HERMES_HOME_BASE"
  else
    PROFILE_HOME="$HERMES_HOME_BASE/profiles/$PROFILE"
  fi
  mkdir -p "$PROFILE_HOME/skills/signed-agent-receipts" "$WORKDIR/bin"
  cp "$WORKDIR/skills/hermes/SKILL.md" "$PROFILE_HOME/skills/signed-agent-receipts/SKILL.md"
  cat > "$WORKDIR/bin/signed-agent-receipts-mcp" <<EOF
#!/usr/bin/env bash
exec "$PYTHON_BIN" -m agent_receipts.mcp_server
EOF
  chmod +x "$WORKDIR/bin/signed-agent-receipts-mcp"
  printf 'Y\n' | hermes --profile "$PROFILE" mcp add signed-agent-receipts --command "$PYTHON_BIN" --args -m agent_receipts.mcp_server || true
fi

OUT="${AGENT_RECEIPTS_SELFTEST_OUT:-$HOME/.config/signed-agent-receipts/output/self-install.jsonl}"
"$PYTHON_BIN" -m agent_receipts self-receipt --out "$OUT" --title "signed-agent-receipts self-install" --summary "Installed from $REPO_URL into profile $PROFILE"
"$PYTHON_BIN" -m agent_receipts verify --jsonl "$OUT"

echo "signed-agent-receipts installed. Self-test receipt: $OUT"
