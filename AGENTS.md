# AGENTS.md

Agent context for this repository lives in [CLAUDE.md](CLAUDE.md).

All agents must follow the same invariants: private pre-launch repo, Ed25519 sign/verify flow, causal evidence only, diffs for code-change receipts, sanitized synthetic examples only, and local signing keys under `~/.config/signed-agent-receipts/` kept out of git.
