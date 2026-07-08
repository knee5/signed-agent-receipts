---
name: signed-agent-receipts
description: "Emit and verify Ed25519-signed receipts for agent work."
version: 0.1.0
---

# Signed Agent Receipts

Use this skill when a PR, fleet job, or review needs a signed receipt proving what an agent did.

## One-line install

```bash
curl -fsSL https://raw.githubusercontent.com/knee5/signed-agent-receipts/main/install.sh | bash
```

For a fresh Hermes profile:

```bash
hermes profile create receipts-smoke
HERMES_PROFILE=receipts-smoke curl -fsSL https://raw.githubusercontent.com/knee5/signed-agent-receipts/main/install.sh | bash
```

Acceptance signal: the installer emits `self-install.jsonl` and `signed-agent-receipts verify` reports `Verified 1/1`.

## Emit a receipt for your work

```bash
signed-agent-receipts self-receipt \
  --out .github/receipts/$(date -u +%Y%m%dT%H%M%SZ)-agent.jsonl \
  --title "agent work receipt" \
  --summary "What changed, why, and test evidence."
signed-agent-receipts verify --jsonl .github/receipts/*.jsonl
```

## Merge gate

Agent-authored PRs must include at least one valid signed receipt JSONL:

```bash
signed-agent-receipts ci-gate --actor "$GITHUB_ACTOR" --receipt-glob '.github/receipts/**/*.jsonl'
```

## MCP server

Add the wrapper to Hermes:

```bash
hermes mcp add signed-agent-receipts --command "python3 -m agent_receipts.mcp_server"
```

Tools exposed:

- `create_signed_receipt`
- `verify_receipt_jsonl`
