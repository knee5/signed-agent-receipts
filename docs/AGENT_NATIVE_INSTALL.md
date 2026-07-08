# Agent-native packaging

This repository can bootstrap itself into agent runtimes without a package-registry release.

## One-line self-install

```bash
curl -fsSL https://raw.githubusercontent.com/knee5/signed-agent-receipts/main/install.sh | bash
```

Fresh Hermes profile smoke test:

```bash
hermes profile create receipts-smoke
HERMES_PROFILE=receipts-smoke curl -fsSL https://raw.githubusercontent.com/knee5/signed-agent-receipts/main/install.sh | bash
```

The installer does four things:

1. Clones/refreshes the repo under `~/.cache/signed-agent-receipts/install`.
2. Installs the Python CLI into the user site.
3. If `hermes` is available, installs the Hermes skill and MCP wrapper into the selected profile.
4. Emits and verifies a signed self-install receipt at `~/.config/signed-agent-receipts/output/self-install.jsonl`.

## MCP wrapper

Manual registration:

```bash
hermes mcp add signed-agent-receipts --command "python3 -m agent_receipts.mcp_server"
```

Exposed tools:

- `create_signed_receipt`
- `verify_receipt_jsonl`

## Agent skills

- Hermes skill: `skills/hermes/SKILL.md`
- Claude Code skill: `skills/claude-code/SKILL.md`
