# signed-agent-receipts

**Agents lie confidently. Receipts don't.**

Your agent says it ran the tests, touched two files, and stayed inside the
workspace. This tool turns that claim into a checkable artifact: it scans
local agent runtime traces (Claude Code, Codex, Hermes), normalizes them into
versioned JSONL records, signs each record with Ed25519, and renders a
Markdown receipt a human can review in a minute. Anyone can verify a receipt
in one command — an altered receipt fails loudly.

> **Have an agent? Put it to work — paste this into it:**
>
> ```
> Read https://raw.githubusercontent.com/knee5/signed-agent-receipts/main/INSTALL-FOR-AGENTS.md and follow it.
> ```
>
> It will install the emitter, sign its own next run, attach the receipt to
> its PR, and ask you — not decide for you — whether to add the verification
> gate to your CI.

**If you are an AI agent reading this:** stop here and read
[AGENTS.md](AGENTS.md). It is addressed to you.

What verification proves: the record hasn't changed since signing, and which
key signed it. What it doesn't prove: that the work was good. That part is
still your job — receipts just make sure you're reviewing what actually
happened. The signing and verification are real; the trace normalizers are
POC-grade and say so below. The full honesty statement — what every layer
does and does not prove — is [SECURITY-MODEL.md](SECURITY-MODEL.md).

## The gate (v0.2)

v0.2 adds the enforcement half: a **receipt that binds work to a PR** and a
**GitHub Action that refuses to take its word for it**. The signed body binds
the SHA-256 of the task the agent was given, the exact `git diff base...head`
of the PR (the gate recomputes this hash independently — never the mutable PR
body), and evidence typed by how the receiver can verify it: `re_executable`,
`ci_attested`, `content_addressed`, or `self_claimed` (displayed, never
counted). Trusted signers and the acceptance policy live in
`.agent-receipts/` on your protected base branch; a PR cannot admit its own
key. Every PR needs a receipt — the only bypass is a maintainer-applied
`human-waiver` label. This repo runs its own gate — see it live in
[docs/DEMO.md](docs/DEMO.md): three open demo PRs, one green, two red, each
for the right reason.

Install and operate: [docs/RECEIPTS-GATE.md](docs/RECEIPTS-GATE.md) ·
Receipt schema: [schema/receipt.schema.json](schema/receipt.schema.json)

---

Durable generated outputs default to a neutral local config path:

```bash
~/.config/signed-agent-receipts/output
```

## Commands

Requires Python 3.10+.

Install locally:

```bash
python -m pip install .
# or
pipx install .
```

Packaging note: the PyPI distribution and installed CLI command are `signed-agent-receipts`; the Python import package remains `agent_receipts` for stable module imports.

Normalize recent local runs:

```bash
signed-agent-receipts normalize --limit 10
```

Render Markdown receipts:

```bash
signed-agent-receipts render --jsonl ~/.config/signed-agent-receipts/output/agent_run.jsonl
```

Run the end-to-end POC:

```bash
signed-agent-receipts dogfood --limit 10
```

Verify signed JSONL receipts:

```bash
signed-agent-receipts verify --jsonl ~/.config/signed-agent-receipts/output/agent_run.jsonl
```

Run tests:

```bash
python -m unittest
```

## What It Scans

The normalizers are heuristic and defensive. Missing or unreadable sources are skipped or represented as warning records instead of crashing.

- Hermes: `~/.hermes`, including readable SQLite, JSON/JSONL, and log/text files.
- Codex: `~/.codex`.
- Claude Code: `~/.claude/projects`.

For tests or local fixture runs, set:

```bash
AGENT_RECEIPTS_HOME=/tmp/fixture-home
AGENT_RECEIPTS_WORKSPACE=/tmp/fixture-workspace
```

## Analytics

The CLI is instrument-ready for PostHog without adding a runtime dependency. Set a
project-scoped key to enable capture; leave it unset for a complete no-op:

```bash
POSTHOG_KEY=phc_... python -m agent_receipts dogfood
# Optional; defaults to https://us.i.posthog.com
POSTHOG_HOST=https://us.i.posthog.com
```

Emitted events currently include `agent_receipts_cli_started`,
`agent_receipts_normalized`, `agent_receipts_rendered`, and
`agent_receipts_dogfood_completed`.

## Signing

Every JSONL receipt written by the CLI is signed with Ed25519 over canonical
JSON: UTF-8 JSON with sorted keys and compact separators, excluding the
`signature` field itself. Markdown receipts include the same signature metadata
in a `Signature` section.

The default private key path is:

```bash
~/.config/signed-agent-receipts/ed25519_private.pem
```

The key is created automatically with `0600` permissions on first use. Override
the path with either `--signing-key /path/to/ed25519_private.pem` or
`AGENT_RECEIPTS_SIGNING_KEY=/path/to/ed25519_private.pem`. Local private-key
filenames are gitignored by default; do not commit private keys.

## Schema Summary

Records are JSONL objects using `schema_version: agent-run-ledger.v1`. The schema lives at [schema/agent_run.schema.json](schema/agent_run.schema.json) and includes:

- identity: run ID, runtime, source path, actor, profile, session, title
- timing: started, ended, duration
- normalized content: inputs, tool calls, file diffs, URLs
- QA support: evidence with local file hashes when available, costs, policy flags
- traceability: receipt path and raw source references
- signature: Ed25519 public key, key ID, canonicalization, and signature value

## Evidence

The POC discovers recent local image files only from causally scoped roots such as the workspace and output directory, computes SHA-256 for local evidence, and records provenance for every evidence item. Shared caches such as `~/.hermes/cache/screenshots` are not backfilled into unrelated runs; time-window matches are marked as heuristic.

## Current Limitations

- Source formats are inferred, not runtime-specific contracts.
- SQLite support samples likely session/run/log tables but does not understand every vendor schema.
- Cost and token data are only populated when present in source traces.
- Markdown receipts are static review artifacts; they do not provide approval workflow state.
- JSON Schema validation is documented but not enforced at runtime.
