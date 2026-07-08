# Agent Run Receipt: Sanitized demo agent run

## Approve / Reject
Suggested decision: **APPROVE CANDIDATE**

- Approve when the listed inputs, tool calls, file diffs, URLs, and evidence match the expected run.
- Reject or escalate when policy flags are high severity, evidence is missing for a critical claim, or file diffs are unexpected.

## Identity
- Run ID: `run_example_0001`
- Runtime: `demo-runtime`
- Source: `examples/source/demo-run.jsonl`
- Actor: example-operator
- Profile: example-profile
- Session: `unknown`
- Started: 2026-07-02T12:00:00+00:00
- Ended: 2026-07-02T12:00:07+00:00
- Duration: 7.0 s

## Inputs
- **user**: Create a sanitized demo receipt from fixture data.

## Tool Calls

| Tool | Status | Elapsed | Args | Artifacts |
| --- | --- | ---: | --- | --- |
| exec_command | ok | 2.0 s | python -m unittest tests.test_signing | examples/receipts/run_example_0001.md |

## File Diffs
- `agent_receipts/signing.py` [added] (+120, -0); provenance: tool_call_id ref=`example-call-0001`

## URLs
- `https://example.test/signed-agent-receipts`

## Evidence
- **url**: Synthetic project URL for public example data. -> `https://example.test/signed-agent-receipts`; provenance: record_url ref=`run_example_0001`

## Costs / Time
- Total: USD 0
- Tokens in: 100
- Tokens out: 40
- Duration: 7.0 s
- Notes: Synthetic example values only.

## Policy Flags
No policy flags extracted.

## Raw Refs
- `examples/source/demo-run.jsonl`

## Signature
- Algorithm: `ed25519`
- Canonicalization: `canonical-json-v1`
- Public key: `OUnbBXjVzCpaKfq/aetifXC/UhMYJzKu8buHyJPLQVw=`
- Key ID: `sha256:09772e2eb97eb241fd0ef5e43b4e4a143fdc1b2598ca9df331561e5b00c75798`
- Signature: `HfMlNqDBMm3LMDG39RibePWMXkLgOXbDawZ+rb+5tqPCn+axQ4IdCUCC83aApzNi35EZrFq2/ZxEjpdbbeJ8Dw==`
