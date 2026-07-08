# receipts-gate adoption block

Copy this workflow into `.github/workflows/receipts-gate.yml` in any repo that wants to reject unsigned agent PRs.

```yaml
name: receipts-gate

on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]

permissions:
  contents: read

jobs:
  receipts-gate:
    name: receipts-gate
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install signed-agent-receipts
        run: python -m pip install git+https://github.com/knee5/signed-agent-receipts.git
      - name: Verify signed receipts for agent PRs
        env:
          GITHUB_ACTOR: ${{ github.actor }}
        run: |
          signed-agent-receipts ci-gate \
            --actor "$GITHUB_ACTOR" \
            --receipt-glob '.github/receipts/**/*.jsonl'
```

Agent PR authors add at least one receipt:

```bash
mkdir -p .github/receipts
signed-agent-receipts self-receipt \
  --out .github/receipts/$(date -u +%Y%m%dT%H%M%SZ)-agent.jsonl \
  --title "agent PR receipt" \
  --summary "Changed: <files>. Tests: <commands>."
```

The check fails if the actor looks agent-like (`[bot]`, `agent`, `claude`, `codex`, `hermes`, `openclaw`) and no valid receipt JSONL is present.
