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
  trusted key, including a stolen one. Execution is confined: no shell
  (allowlist entries are argv-split, so metacharacters are inert), and the
  working directory must resolve — after symlinks — inside the verification
  worktree. A receipt that points `cwd` outside the worktree is trying to
  pass its command in a directory with none of the PR's code in it.
- `ci_attested` — GitHub (a third party relative to the agent) reports a
  check run **at the signed head_sha** concluded successfully. The receipt
  does not get to choose the commit: a receipt-supplied `sha` is accepted
  only if it IS the signed head, and the returned check run's own `head_sha`
  must match — otherwise a receipt could borrow a green check from an
  unrelated commit. Caveat: a check attests whatever the workflow file at
  that commit does. If the PR modifies `.github/workflows/**`, the gate
  discounts CI attestations for that PR
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

## Timestamps are self-asserted — with two verifier-clock floors

`issued_at` and the key's claimed `valid_from`/`valid_until` are written by
the signer. Within one org, where the machine clock and key handling are
yours, window checks meaningfully bound what an old stolen key can sign.
Across org boundaries, a signer who lies about time defeats them. Do not
treat timestamps from keys you don't operate as facts; the enforceable window
is the one *you* grant in your `trusted_signers.yml`.

Two checks use the **verifier's** clock and cannot be defeated by a lying
signer: a receipt whose `issued_at` is in the verifier's future (beyond a
small skew) is rejected, and a key whose trusted window has expired verifies
nothing — regardless of what the receipt claims about when it was signed.
What remains signer-defeatable, and stays open: a stolen key can *backdate*
`issued_at` to a moment inside a still-valid window.

## The gate must run from a trusted ref

Everything above assumes the gate code doing the checking is the code you
reviewed. That assumption is destroyed by workflow wiring that checks out a
PR and then executes the gate *from that checkout* (`uses: ./` after a PR
checkout, or installing the package from the PR's working copy): any PR
could then rewrite `gate.py` to say yes to itself, and every guarantee in
this document evaporates without a trace in the gate's own output.

So: consumers must `uses:` a **pinned tag or commit SHA** of this action,
and this repo's own dogfood workflow checks out the PR as *data* and runs
the gate from a **second checkout of the base ref** — a ref a PR author
cannot influence. `tests/test_workflow_security.py` pins that wiring.
CODEOWNERS must cover not just `.agent-receipts/` but the gate code
(`agent_receipts/`), the schema, the packaging metadata, and the workflows —
rewiring the workflow is equivalent to rewriting the gate.

## The trust anchor is only as protected as your base branch

The gate reads `trusted_signers.yml` and `policy.yml` **only** from the PR's
base branch, and fails any PR that touches `.agent-receipts/**`. That kills
"the PR admits its own key" — provided your repo settings hold the line:

1. Branch protection on the default branch, with `receipts-gate` as a
   **required** status check (otherwise a red gate is advisory).
2. Required code-owner review, with `/.agent-receipts/` — and the gate's
   whole surface: `/agent_receipts/`, `/.github/`, `/action.yml`,
   `/schema/`, `/pyproject.toml` — in CODEOWNERS (otherwise anyone with
   write can merge a config or verifier change directly).

A PR that touches `.agent-receipts/**` fails the gate outright, and the
waiver label does not override that. Landing a config change therefore
requires a repo-settings-level bypass by an admin (GitHub records it), not
a label.

Neither can be enforced from inside this repository's files. The gate tells
you the truth about a PR; whether an unreviewed merge to the base branch was
possible is between you and your repo settings.

## Why the gate covers ALL PRs

"Only gate agent PRs" is unenforceable: an agent pushing under a human's
token is indistinguishable from the human. So every PR requires a receipt,
and the only bypass is the `human-waiver` label. Label *presence* proves
nothing — any token with triage rights, including a bot's, can apply labels,
and a bot holding a maintainer's PAT can label its own PR. The gate
therefore honors a waiver only after confirming, via the GitHub API, that
the label was applied by a user holding write, maintain, or admin on the
repo. If it cannot prove who applied the label (no token, API failure), the
waiver is refused — fail closed. Two hard limits, stated plainly: the
waiver never bypasses the `.agent-receipts/**` tamper check (waiving
verification is not approving a change to who is trusted), and a waiver
applied *with* a compromised maintainer credential still passes — the waiver
is an authenticated, audited bypass, not a review guarantee.

## Known residual risks, stated plainly

- **Stolen keys.** Ed25519 signs whatever the key holder wants. Rotation
  (`revoked_keys`, validity windows) limits the blast radius; it cannot
  retroactively distinguish honest receipts from forged ones signed inside
  the validity window — including receipts *backdated* into that window.
- **Trusted-but-lying signers.** The gate verifies what it can recompute.
  A trusted signer can still claim `self_claimed` nonsense (displayed, never
  counted) or do bad work with green tests. Receipts move review from "did
  it happen" to "was it good" — they do not replace review.
- **GitHub as oracle.** `ci_attested` trusts GitHub's check-run API, and the
  waiver trusts GitHub's label-event and permission APIs. If your threat
  model includes GitHub lying to you, no artifact in this repo helps.
- **Compromised maintainer credentials.** A waiver applied with a stolen
  maintainer token verifies as a maintainer waiver. The gate leaves an
  audit trail (who, when); it cannot detect that the "who" was not at the
  keyboard.
- **Bootstrap mode has no expiry.** Until a trust anchor is merged to the
  base branch, every PR passes with a loud NOT CONFIGURED notice — forever.
  Nothing inside the repo can force arming; only the notice and your own
  process do. A policy file without an anchor fails closed rather than
  demoting to bootstrap, so half-deleted config cannot masquerade as
  never-configured.
- **Ledger writes are serialized per machine, not globally.** The consumed-
  nonce ledger takes an exclusive `flock` during check-then-append, so
  concurrent consumers on one host cannot double-claim a nonce; on platforms
  without `fcntl` (Windows) the lock is skipped. Across machines,
  serialization comes from the ledger living in git — racing consumers
  produce a push conflict, not a silent double-spend.
- **Emitter-side normalizers are heuristic** (v1 run-ledger records). The
  v0.2 binding fields (hashes, SHAs) are exact; the descriptive run-trace
  fields are best-effort and marked as such.
