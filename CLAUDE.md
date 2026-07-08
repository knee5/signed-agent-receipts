# CLAUDE.md

## Repository

`signed-agent-receipts` is a PUBLIC repository (launched 2026-07) for signed receipts of AI-agent work. Two layers: (1) run-ledger v1 — normalizes agent traces into JSONL/Markdown receipts, Ed25519-signed; (2) receipt v0.2 + receipts-gate — a signed binding of request → PR diff → typed evidence, enforced as a GitHub required check. Everything public here is a trust claim: every statement in code, docs, and receipts must be literally true.

## Commands

```bash
python3 -m unittest
pipx install .
signed-agent-receipts normalize --limit 10
signed-agent-receipts render --jsonl ~/.config/signed-agent-receipts/output/agent_run.jsonl
signed-agent-receipts dogfood --limit 10
signed-agent-receipts verify --jsonl ~/.config/signed-agent-receipts/output/agent_run.jsonl
# v0.2 (see docs/RECEIPTS-GATE.md):
signed-agent-receipts receipt --request-file task.md --pr 42 --base origin/main
signed-agent-receipts verify-receipt --receipt receipts/pr-42.receipt.json
signed-agent-receipts gate --event "$GITHUB_EVENT_PATH" --repo-dir .
signed-agent-receipts consume --receipt receipts/pr-42.receipt.json
```

## Gate invariants (v0.2)

- `.agent-receipts/` is the trust anchor; the gate reads it ONLY from the base branch, and PRs touching it must fail pending maintainer review. Do not add code paths that read gate config from the PR head.
- `self_claimed` evidence never satisfies policy, anywhere.
- The gate never executes a receipt-supplied command unless the exact string is in the base-branch policy allowlist.
- The canonical diff command (agent_receipts/gitdiff.py) is a compatibility contract — changing any flag breaks every existing diff_hash. Version it, don't edit it.

## Provenance invariant

Evidence must be causally linked to the run being receipted. Never backfill evidence from shared caches, ambient screenshot folders, old browser downloads, unrelated workspaces, or timestamp-only guesses. If causality is uncertain, mark the evidence heuristic or omit it.

Code-change receipts require diffs. A receipt that claims code was changed must include the corresponding file diff metadata/provenance; do not rely only on a prose summary.

## Examples rule

`examples/` must remain sanitized and synthetic. Do not place data from private home directories, real agent sessions, real customer/product sessions, secrets, tokens, or personal file paths in examples. Use `example.test`, synthetic actors, and repo-relative fixture paths.

## Signing keys

The default signing key directory is `~/.config/signed-agent-receipts/`; the default private key is `~/.config/signed-agent-receipts/ed25519_private.pem`. Signing keys are local-only and must stay gitignored. Do not commit private keys.
