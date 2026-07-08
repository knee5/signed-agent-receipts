# The gate, live: three PRs you can click

This repo runs its own receipts-gate, and three open pull requests are left
open on purpose as working exhibits. Same repo, same check, same policy —
one passes, two fail, each for exactly the reason it should.

| Exhibit | PR | receipts-gate | Why |
|---|---|---|---|
| Receipted agent PR | [#5](https://github.com/knee5/signed-agent-receipts/pull/5) | **pass** | Signed v0.2 receipt: pinned signer, recomputed diff hash, request bound to the issuer's task file |
| Same class of change, no receipt | [#7](https://github.com/knee5/signed-agent-receipts/pull/7) | **fail** | Nothing to verify — the default state of agent PRs today |
| Work pushed after signing | [#8](https://github.com/knee5/signed-agent-receipts/pull/8) | **fail** | Delivered head is not the signed head — receipts don't survive tampering |

## Exhibit A — the receipted PR ([#5](https://github.com/knee5/signed-agent-receipts/pull/5))

A small real docs change, delivered with a signed `agent-receipt.v0.2`
(`receipts/pr-5.receipt.json`, attached sign-then-attach as a trailing
receipts-only commit). Green here means the gate verified, independently:

- **the signer** — Ed25519 signature by a key pinned in
  `.agent-receipts/trusted_signers.yml` on `main`. A PR cannot admit its own key.
- **the diff** — the gate recomputes the canonical `git diff base...head`
  hash itself; the mutable PR body is never trusted.
- **the task** — `request.hash` equals the SHA-256 of `tasks/pr-5.md`, which
  the issuer committed to `main`. The agent cannot grade its own homework by
  hashing a task it wrote for itself.
- **the evidence** — typed items checked against `.agent-receipts/policy.yml`:
  the allowlisted `python -m unittest` is re-executed at the signed head, the
  content-addressed file is re-hashed. `self_claimed` prose is displayed and
  never counted.

## Exhibit B — the twin without a receipt ([#7](https://github.com/knee5/signed-agent-receipts/pull/7))

The same class of change — a small real docs improvement — delivered the way
most agent PRs arrive today: prose claims, no receipt. The check says:

```
[ FAIL ] no agent-receipt.v0.2 receipt found under receipts/ for
knee5/signed-agent-receipts#7. All PRs require a receipt; the only bypass
is the maintainer-applied 'human-waiver' label.
```

Nothing is wrong with the change. What's missing is provenance — there is
nothing here a machine can check.

## Exhibit C — work pushed after signing ([#8](https://github.com/knee5/signed-agent-receipts/pull/8))

A receipt was emitted and attached honestly. Then one more work commit was
pushed on top — the classic "the agent kept working after it signed". The
receipt is still cryptographically valid; the gate refuses anyway:

```
[ warn ] receipt receipts/pr-8.receipt.json: commits after the signed
head_sha touch non-receipt paths: INSTALL-FOR-AGENTS.md. Only receipts/**
may change after signing (sign-then-attach).
[ FAIL ] no receipt for this PR passed verification + policy
```

The sibling case — force-push/rebase under the receipt — draws the sibling
refusal: `STALE: signed head_sha ... is not an ancestor of PR head ...
Re-emit the receipt for the new head.` Either way the remedy is honest and
cheap: re-emit for the head you actually deliver.

## The human path — waivers with an audit trail

Human PRs don't emit receipts. The bypass is a `human-waiver` label, honored
only after the gate confirms via the GitHub API that a user with write access
applied it — label presence alone is never enough. The issuer PRs that
carried the task files for these exhibits went through exactly that flow:

```
[  ok  ] waiver label 'human-waiver' applied by 'knee5' (verified repo permission: admin)
[ warn ] WAIVED: ... the waiver is the audit trail that a human took
responsibility for this merge.
```

## What a green check proves — and what it doesn't

It proves the delivered diff is byte-exactly what a pinned key signed,
against the task the issuer dispatched, with evidence the receiver could
verify or re-execute. It does **not** prove the work is good, and a
signature does not make a claim true — `self_claimed` text rides along
unverified and is never counted. Judgment stays human; the gate just makes
sure the humans are judging what actually happened. The full boundary is
[SECURITY-MODEL.md](../SECURITY-MODEL.md).

## Check it yourself — don't take this page's word for it

```bash
git clone https://github.com/knee5/signed-agent-receipts && cd signed-agent-receipts
python3 -m pip install "git+https://github.com/knee5/signed-agent-receipts"   # v0.2 is not on PyPI yet

git fetch origin pull/5/head:demo-pass && git checkout demo-pass
signed-agent-receipts verify-receipt --receipt receipts/pr-5.receipt.json
```

Then try to cheat: change any byte of the receipt, or any byte of the diff,
and verify again. It fails loudly. That's the product.

## Run this on your own repo

One workflow file plus two config files under maintainer review —
[RECEIPTS-GATE.md](RECEIPTS-GATE.md). Until you merge a trust anchor, the
gate runs in bootstrap mode (loud notice, no enforcement), so adopting it
never bricks a repo.
