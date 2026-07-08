# Security model

This document says plainly what a receipt and the gate prove, what they do
not, and where the sharp edges are. Precision here is the whole product: a
provenance tool that oversells its own guarantees is the failure mode it
exists to fix.

## What a signature proves — and what it doesn't

A valid Ed25519 signature over a v0.2 receipt proves exactly three things:

1. **Authorship** — the holder of the private key for `key_id` signed it.
2. **Integrity** — no field in the signed body changed since signing.
3. **Binding** — the signer committed to a specific request hash, a specific
   `base_sha...head_sha` diff of a specific PR in a specific repo, specific
   evidence claims, and a specific time window and audience.

It does **not** prove the work is correct, complete, safe, or that any prose
in the receipt is true. A receipt is a signed *claim structure*. The gate's
job is to check the parts of the structure that are independently checkable
(the diff hash, file hashes, CI conclusions, allowlisted re-executions) and
to refuse to let the rest masquerade as verified.

## Evidence classes are ranked by receiver-verifiability

- `re_executable` — the receiver reruns a command and observes the result.
  Strongest, but the gate executes **only** commands allowlisted in the
  base-branch policy. Receipts are signed, not trusted: executing arbitrary
  receipt-supplied commands would hand CI code execution to anyone holding a
  trusted key, including a stolen one.
- `ci_attested` — GitHub (a third party relative to the agent) reports a
  check run at the bound commit concluded successfully. Caveat: a check
  attests whatever the workflow file at that commit does. If the PR modifies
  `.github/workflows/**`, the gate discounts CI attestations for that PR
  (`distrust_ci_when_workflows_change`).
- `content_addressed` — the receiver hashes a repo file at the signed head
  and compares. Proves the artifact is the one named; says nothing about its
  quality.
- `self_claimed` — output of the process being evaluated. Displayed for
  human review, **never** satisfies policy, and cannot be listed as a policy
  requirement. `judgment_calls` is the same category: optional disclosure,
  never a gate input.

## Request binding is only as strong as the verifier's copy of the request

`request.hash` exists to close the task-substitution hole: without it an
agent can solve an easier task than asked and sign a perfectly
self-consistent receipt. But a hash only helps a verifier who knows what the
request was. The gate checks `request.hash` only when the workflow provides
an issuer-held source (`request-source-file` on the base branch, or
`request-hash`). Otherwise it reports the hash as present-but-unchecked. An
agent hashing a request it wrote for itself passes the unchecked case by
construction — issuers who care must supply the request.

## Attestation is not authorization

A receipt says "this key produced this diff with this evidence." It does not
say the key holder was *allowed* to do the work, spend the budget, or touch
the systems involved. The confused-deputy problem — an agent laundering a
privileged action through a service that trusts it — lives in the
authorization layer, and this project does not provide one. The reserved
`authorization` field exists so receipts can *carry* a reference to an
external authorization/intent token without a schema break; carrying one
still doesn't make the gate an authorization system.

## Replay and freshness exist only at stateful gates

The gate deduplicates nonces against `.agent-receipts/consumed.jsonl` on the
base branch, and the `audience`/`pr_number`/`head_sha` bindings stop
cross-repo and cross-PR reuse. But a **stateless** verifier — someone running
`verify-receipt` on a laptop, a fresh CI runner with no ledger — cannot know
whether a receipt was already used. If you need freshness, you need state on
the verifying side, or an interactive challenge (the nonce in v0.2 is
emitter-generated randomness for dedup, not a verifier-issued challenge).

## Timestamps are self-asserted

`issued_at` and the key's claimed `valid_from`/`valid_until` are written by
the signer. Within one org, where the machine clock and key handling are
yours, window checks meaningfully bound what an old stolen key can sign.
Across org boundaries, a signer who lies about time defeats them. Do not
treat timestamps from keys you don't operate as facts; the enforceable window
is the one *you* grant in your `trusted_signers.yml`.

## The trust anchor is only as protected as your base branch

The gate reads `trusted_signers.yml` and `policy.yml` **only** from the PR's
base branch, and fails any PR that touches `.agent-receipts/**`. That kills
"the PR admits its own key" — provided your repo settings hold the line:

1. Branch protection on the default branch, with `receipts-gate` as a
   **required** status check (otherwise a red gate is advisory).
2. Required code-owner review, with `/.agent-receipts/` in CODEOWNERS
   (otherwise anyone with write can merge a config change directly).

Neither can be enforced from inside this repository's files. The gate tells
you the truth about a PR; whether an unreviewed merge to the base branch was
possible is between you and your repo settings.

## Why the gate covers ALL PRs

"Only gate agent PRs" is unenforceable: an agent pushing under a human's
token is indistinguishable from the human. So every PR requires a receipt,
and the only bypass is a `human-waiver` label — which GitHub only lets users
with triage permission or higher apply, giving you a maintainer-signed audit
trail instead of a silent hole.

## Known residual risks, stated plainly

- **Stolen keys.** Ed25519 signs whatever the key holder wants. Rotation
  (`revoked_keys`, validity windows) limits the blast radius; it cannot
  retroactively distinguish honest receipts from forged ones signed inside
  the validity window.
- **Trusted-but-lying signers.** The gate verifies what it can recompute.
  A trusted signer can still claim `self_claimed` nonsense (displayed, never
  counted) or do bad work with green tests. Receipts move review from "did
  it happen" to "was it good" — they do not replace review.
- **GitHub as oracle.** `ci_attested` trusts GitHub's check-run API. If your
  threat model includes GitHub lying to you, no artifact in this repo helps.
- **Emitter-side normalizers are heuristic** (v1 run-ledger records). The
  v0.2 binding fields (hashes, SHAs) are exact; the descriptive run-trace
  fields are best-effort and marked as such.
