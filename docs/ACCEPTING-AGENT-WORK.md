# Accepting work from agents you don't control

You are already merging pull requests written by AI agents. Maybe your own — a
Claude Code or Codex run you kicked off and skimmed. Increasingly, someone
else's: a contributor whose "contribution" is an agent you never see. The PR
description says it added a feature, ran the tests, and touched three files.
You have two options today: read every line yourself, or take its word.

This is the same problem invoices solved for commerce. You don't
re-manufacture a supplier's goods to check them — you demand a document that's
**attributable, tamper-evident, and costly to fake well**, and you spot-check
it. A receipt is that document for agent work. This page is the verifier's
side of the story: not how to *produce* a receipt (that's
[INSTALL-FOR-AGENTS.md](../INSTALL-FOR-AGENTS.md)), but why you'd *require* one
before you merge, and what it actually buys you.

## What a receipt lets you decide that you couldn't before

1. **Did the work I'm merging match the task I gave?** The receipt binds the
   SHA-256 of the *original request* to the *exact diff*. An agent that quietly
   solved an easier problem than asked — and wrote a self-consistent summary of
   the easier problem — fails this. Deliverable-binding alone can't catch that;
   request-binding can.
2. **Is this the work that was signed, or work pushed after?** The gate
   recomputes `git diff base...head` itself and checks it against the signed
   head. A force-push-after-signing shows up as stale. (See
   [docs/DEMO.md](DEMO.md) — one of the demo PRs is red for exactly this
   reason.)
3. **How strong is the evidence, actually?** Evidence is *typed by how you can
   check it*: `re_executable` (a command you can run), `ci_attested` (a GitHub
   CI run the gate fetches from GitHub itself), `content_addressed` (a hash you
   can recompute) — versus `self_claimed` prose, which the receipt **displays
   but never counts**. Your policy says "code PRs need re-executable or CI
   evidence; docs can pass on prose." The gate enforces that, so "the agent
   said it ran the tests" stops being a merge-worthy claim.
4. **Do I trust the key that signed this?** Trusted signers live in
   `.agent-receipts/trusted_signers.yml` on your *protected base branch*,
   CODEOWNERS-locked. A PR cannot add its own key. Revoked keys are checked
   every run. Key windows (`valid_from` / `valid_until`) mean a stolen key
   doesn't forge forever.

## What it does not do — and why that's the point

A signature proves *authorship and integrity, not truth*. A lying agent can
sign a lying receipt. What the gate removes is the *cheap* lie: unattributable
claims, tampered diffs, work-after-signing, prose masquerading as evidence,
keys that admit themselves. What remains — an agent that fabricates
re-executable evidence well enough to survive your spot-check — is expensive,
attributable, and exactly the narrow surface your review time should go to.
The honesty is the product; the full statement of what every layer does and
doesn't prove is in [SECURITY-MODEL.md](../SECURITY-MODEL.md).

## Where this sits next to the rest of the field

Signing what an agent *did* is becoming table stakes — audit protocols chain
an agent's tool calls, network firewalls sign its egress traffic, build systems
sign their artifacts. Some of those can even fail a CI check. But they all
attest the agent's *behavior* — what it touched, where it called out to. This
gate is the one that attests the *deliverable*: it takes the coding-agent
session logs already on your disk, turns them into a receipt bound to a
specific PR diff, and won't let that PR merge unless the diff clears your
policy. Run it alongside the others, not instead of them — they answer "did the
agent misbehave while it ran?"; this answers "should this diff land?"

## Adopt it in five minutes (verifier side)

1. Add `.github/workflows/receipts-gate.yml` pinning
   `knee5/signed-agent-receipts` at a tag/SHA (never `uses: ./` — the gate must
   not run from the PR it judges). Full workflow in
   [RECEIPTS-GATE.md](RECEIPTS-GATE.md).
2. Commit `.agent-receipts/trusted_signers.yml` (the keys you accept) and
   `.agent-receipts/policy.yml` (a starter policy ships in this repo) to your
   base branch.
3. Make `receipts-gate` a required status check in branch protection.

Now every code PR either carries a receipt your policy accepts, or a maintainer
consciously applies the `human-waiver` label with an audit trail. "An agent
opened a PR and it merged because nobody looked" stops being possible by
default.

> Live proof, on this repo's own gate: three open demo PRs — one green
> (receipted, valid), two red (no receipt; work pushed after signing) — each
> failing for the specific reason above. See [docs/DEMO.md](DEMO.md).
