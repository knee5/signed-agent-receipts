# POC Days 1-5

## Goal

Ship a small, local-only proof of concept for `signed-agent-receipts`: normalize recent agent run traces into a ledger and render human-reviewable QA receipts.

## Day 1: Ledger Shape

- Define `agent-run-ledger.v1` JSON Schema.
- Create stdlib JSONL read/write helpers.
- Establish durable output default: `~/.config/signed-agent-receipts/output`.

Success bar: a fixture record can be written, read, and mapped to the schema fields.

## Day 2: Runtime Normalizers

- Scan Hermes, Codex, and Claude Code local paths when present.
- Parse JSON, JSONL, SQLite, and text/log fallbacks.
- Redact obvious secrets and truncate long text.

Success bar: absent sources never crash; present fixture sources produce useful records.

## Day 3: Evidence Pack

- Attach local screenshot/image evidence when found in workspace or output roots.
- Hash local evidence files with SHA-256.
- Fall back to URL or source artifact evidence.

Success bar: every rendered receipt has at least one evidence pointer when source data exists.

## Day 4: Markdown Receipts

- Render approve/reject guidance first.
- Include identity, inputs, tool calls, diffs, URLs, evidence, costs/time, flags, and raw refs.
- Keep one receipt readable without opening the transcript.

Success bar: a human can approve, reject, or ask for follow-up from the receipt alone.

## Day 5: Dogfood and Tests

- Add `normalize`, `render`, and `dogfood` CLI commands.
- Cover redaction/truncation, JSONL, Markdown rendering, and temp-fixture dogfood with `unittest`.
- Run dogfood into the default local output directory.

Success bar: `python -m unittest` passes and `python -m agent_receipts dogfood --limit 10` produces JSONL plus Markdown receipts.
