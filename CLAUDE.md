# CLAUDE.md

## Repository

`signed-agent-receipts` is a private pre-launch repository for signed receipts of AI-agent runs. It normalizes agent traces into JSONL/Markdown receipts and signs/verifies them with Ed25519 so reviewers can inspect provenance without rereading full sessions.

Keep the GitHub repository PRIVATE pending launch. Do not change repository visibility.

## Commands

```bash
python3 -m unittest
pipx install .
signed-agent-receipts normalize --limit 10
signed-agent-receipts render --jsonl ~/.config/signed-agent-receipts/output/agent_run.jsonl
signed-agent-receipts dogfood --limit 10
signed-agent-receipts verify --jsonl ~/.config/signed-agent-receipts/output/agent_run.jsonl
```

## Provenance invariant

Evidence must be causally linked to the run being receipted. Never backfill evidence from shared caches, ambient screenshot folders, old browser downloads, unrelated workspaces, or timestamp-only guesses. If causality is uncertain, mark the evidence heuristic or omit it.

Code-change receipts require diffs. A receipt that claims code was changed must include the corresponding file diff metadata/provenance; do not rely only on a prose summary.

## Examples rule

`examples/` must remain sanitized and synthetic. Do not place data from private home directories, real agent sessions, real customer/product sessions, secrets, tokens, or personal file paths in examples. Use `example.test`, synthetic actors, and repo-relative fixture paths.

## Signing keys

The default signing key directory is `~/.config/signed-agent-receipts/`; the default private key is `~/.config/signed-agent-receipts/ed25519_private.pem`. Signing keys are local-only and must stay gitignored. Do not commit private keys.
