---
name: signed-agent-receipts-claude-code
description: "Claude Code workflow for signed agent receipts on PRs."
version: 0.1.0
---

# Signed Agent Receipts for Claude Code

Before opening or updating a PR, attach a signed receipt generated from the current branch context.

## Install

```bash
python3 -m pip install --user git+https://github.com/knee5/signed-agent-receipts.git
```

## PR receipt ritual

1. Summarize the work in one causal paragraph: files changed, tests run, and any gaps.
2. Emit a signed receipt under `.github/receipts/`.
3. Verify it locally before pushing.

```bash
mkdir -p .github/receipts
signed-agent-receipts self-receipt \
  --out .github/receipts/$(date -u +%Y%m%dT%H%M%SZ)-claude-code.jsonl \
  --title "Claude Code PR receipt" \
  --summary "Changed: <files>. Tests: <commands>. Gaps: <none/describe>."
signed-agent-receipts verify --jsonl .github/receipts/*.jsonl
git add .github/receipts
```

Do not claim tests passed unless the command actually ran. If no causal evidence exists, say so in the summary rather than backfilling from unrelated logs.
