# receipts-gate: install and operate

The gate makes "this PR carries a verifiable receipt" a required CI check.
It verifies signature, trusted signer, key windows, and revocations; it
independently recomputes the PR diff hash; it verifies typed evidence; and it
applies your acceptance policy. Read [SECURITY-MODEL.md](../SECURITY-MODEL.md)
first — it says exactly what this does and does not prove.

Jump to: [workflow](#1-add-the-workflow) ·
[trust anchor + policy](#2-add-the-trust-anchor-and-policy-on-your-default-branch) ·
[repo settings](#3-lock-the-config-down-repo-settings--required) ·
[attaching receipts](#4-how-agents-attach-receipts-sign-then-attach) ·
[request binding](#5-request-binding-how-an-issuer-supplies-the-expected-request) ·
[canonical diff](#7-the-canonical-diff-exactly) ·
[failure modes](#failure-modes-you-will-actually-see)

## 1. Add the workflow

```yaml
# .github/workflows/receipts-gate.yml
name: receipts-gate

on:
  pull_request:
    types: [opened, synchronize, reopened, labeled, unlabeled]

permissions:
  contents: read
  checks: read
  issues: read          # waiver verification: who applied the label
  pull-requests: read

jobs:
  receipts-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0          # the gate recomputes diffs; it needs history
      - uses: knee5/signed-agent-receipts@<commit-sha>   # PIN a tag or SHA — see warning below
        with:
          github-token: ${{ github.token }}
```

`labeled`/`unlabeled` matter: applying the waiver label re-runs the gate
instead of leaving a stale failing check.

**The gate must never execute from the PR it is judging.** Pinning a tag or
SHA of this action gives you that: the action installs its own pinned copy,
and the PR checkout is only the data being verified. It also runs both of its
python steps in isolated mode (`python -I`) from a scratch directory, so a PR
that drops a `pip.py` or `agent_receipts/` into the workspace cannot get its
code imported ahead of the trusted install — you get this for free by pinning
the action. What you must NOT do is run a local copy of the gate out of the
PR checkout — `uses: ./`, or `pip install .` / `python -m ...` from the
checked-out PR — because then any PR can rewrite the verifier and approve
itself. If you vendor the gate in the same repo it guards (as this repo
does), check out the base ref into a second directory, install with
`python -I -m pip install --isolated`, and run with `python -I -m
agent_receipts` from outside the PR checkout; see this repo's own
[receipts-gate.yml](../.github/workflows/receipts-gate.yml) and the wiring +
isolation tests in `tests/test_workflow_security.py`.

## 2. Add the trust anchor and policy on your default branch

`.agent-receipts/trusted_signers.yml`:

```yaml
version: 1
signers:
  - name: my-agent
    key_id: "sha256:<fingerprint from the agent's receipt>"
    public_key: "<base64 raw Ed25519 public key from the receipt>"
    valid_from: "2026-07-08T00:00:00+00:00"
    valid_until: "2027-07-08T00:00:00+00:00"
revoked_keys: []
```

`.agent-receipts/policy.yml` — see this repo's own
[.agent-receipts/policy.yml](../.agent-receipts/policy.yml) for a commented
example. The shape: rules map path globs to the evidence methods that are
acceptable for those paths (first matching rule wins, unmatched paths use
`default_require`, which defaults to the strong classes).

Once a trust anchor exists, `policy.yml` is **required and must be explicit**.
An armed gate will not fall back to built-in defaults if the file is missing
(a reverted or half-merged policy fails closed instead of silently relaxing),
and it rejects a policy that never states `settings.require_request_binding`
— leaving it implicit would silently disable request binding. Set it to
`true` or `false` on purpose. Everything else keeps safe defaults, but that
one control must be declared.

Until `trusted_signers.yml` exists on the base branch, the gate runs in
**bootstrap mode**: it reports receipts and passes with a loud
"NOT CONFIGURED" notice, so adopting the workflow never bricks the repo.
Merging the trust anchor arms it. (A `policy.yml` on the base branch *without*
a `trusted_signers.yml` is treated as half-configured and fails closed — it
will not silently drop to bootstrap.)

## 3. Lock the config down (repo settings — required)

The gate reads both config files ONLY from the PR's base branch and fails
any PR touching `.agent-receipts/**`. For that to mean anything:

1. **Branch protection** on your default branch, with `receipts-gate` as a
   **required status check**.
2. **CODEOWNERS**: cover `/.agent-receipts/` AND the paths that define what
   the gate is — the verifier code, workflows, action, schema, and packaging
   (see this repo's [CODEOWNERS](../.github/CODEOWNERS)) — and require
   code-owner review in branch protection.

The waiver label does **not** bypass the config-tamper failure: waiving
verification is not the same as approving a change to who is trusted.
Config changes land via maintainer-reviewed PRs merged with a
repo-settings-level bypass (branch-protection admin override), which GitHub
audits. The gate's own failure on config PRs is by design.

### The waiver, precisely

A waiver is honored only when the gate can verify — via the GitHub API —
that the `human-waiver` label was applied by a user with write, maintain, or
admin permission on the repo. Label presence alone is never enough (any
triage-capable token, including a bot's, can apply labels). If the applier
cannot be identified or their permission cannot be confirmed, the gate says
so and fails closed.

## 4. How agents attach receipts (sign-then-attach)

The emitter binds the WORK head, then the receipt is committed on top; the
gate allows trailing commits that touch only `receipts/**`:

```bash
# on the PR branch, work committed, PR opened as #42
signed-agent-receipts receipt \
  --request-file task.md \
  --pr 42 \
  --base origin/main \
  --evidence-file evidence.json      # optional; typed items
git add receipts/ && git commit -m "attach receipt" && git push
```

Any later change to non-receipt paths (including force-pushes) makes the
receipt stale; re-emit for the new head. If CI needs to finish before you can
reference it as `ci_attested` evidence, push the work, wait for the check,
then emit + attach.

Evidence file example (`evidence.json`):

```json
[
  {"method": "ci_attested", "provider": "github", "check_name": "python-ci"},
  {"method": "re_executable", "cmd": "python -m unittest", "expected_exit_code": 0},
  {"method": "content_addressed", "path": "schema/receipt.schema.json",
   "sha256": "sha256:<hex>"},
  {"method": "self_claimed", "claim": "Manually checked the rendered docs."}
]
```

`re_executable` runs only if the exact command string is in the policy's
`re_executable_allowlist` — and it runs without a shell (the allowlisted
string is argv-split; pipes and redirects are inert) in a working directory
confined to the verification worktree. `self_claimed` is displayed, never
counted.

## 5. Request binding (how an issuer supplies the expected request)

Without an issuer-held copy of the task, `request.hash` is reported but
unchecked, and an agent that hashed a task it wrote for itself passes by
construction. To close that, the ISSUER — someone with write access to the
base branch — commits the exact task bytes there, and the workflow points
the gate at them. The agent cannot supply its own "request": the file lives
on the protected base, not in the PR.

The per-PR convention this repo uses (`require_request_binding: true` in its
policy): when dispatching work that lands as PR `N`, merge the task bytes to
the base branch as `tasks/pr-N.md`, and let the workflow interpolate:

```yaml
          request-source-file: tasks/pr-${{ github.event.pull_request.number }}.md
```

The gate then requires `request.hash` to equal the SHA-256 of that file's
bytes on the base branch. For a fixed task file, pass its path directly; for
issuers who keep the task text private, pass `request-hash` with the
`sha256:<hex>` instead. If the policy sets `require_request_binding: true`
and no source reaches the gate (file missing on base included), receipts
fail — human PRs without a task file go through the maintainer waiver.

## 6. The consumed-nonce ledger (replay dedup)

The gate rejects a receipt whose nonce already appears in
`.agent-receipts/consumed.jsonl` on the base branch (unless it is the same
receipt on the same PR — re-runs are idempotent). To record consumption,
append after merge:

```bash
signed-agent-receipts consume \
  --receipt receipts/pr-42.receipt.json \
  --ledger .agent-receipts/consumed.jsonl \
  --pr 42
# commit the ledger change to the default branch via your normal review flow
```

In practice the PR bindings (repo + PR number + head SHA + audience) already
stop most reuse; the ledger closes the remaining same-PR-number/recreated-PR
cases and matters more for future non-PR audiences. This repo ships the
command and checks the ledger, but does not auto-commit to main — a bot
pushing to a protected default branch is your call, not ours.

## 7. The canonical diff, exactly

`deliverable.diff_hash` is the SHA-256 of the bytes produced by:

```bash
GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null LC_ALL=C \
git -c core.quotePath=true -c diff.suppressBlankEmpty=false \
    diff --no-color --no-ext-diff --no-textconv --no-renames \
    --full-index --binary --diff-algorithm=myers -U3 \
    --inter-hunk-context=0 --src-prefix=a/ --dst-prefix=b/ \
    --ignore-submodules=none <base_sha>...<head_sha> --
```

Anyone can recompute it without this package. Three dots = merge-base diff
(what the GitHub PR view shows); the output is fully determined by the two
SHAs. The gate additionally requires `base_sha` to be an ancestor of the
base branch tip, so a receipt cannot shrink its own diff by picking a bogus
base.

## Failure modes you will actually see

| Gate says | It means |
|---|---|
| `no agent-receipt.v0.2 receipt found` | PR has no receipt for this repo+PR under `receipts/`. Emit one, or a maintainer applies `human-waiver`. |
| `diff hash mismatch` | The delivered diff is not the diff that was signed. |
| `STALE: signed head_sha is not an ancestor` | Force-push/rebase after signing. Re-emit. |
| `commits after the signed head_sha touch non-receipt paths` | Work was added after signing. Re-emit. |
| `PR modifies gate configuration` | `.agent-receipts/**` changed. Maintainer review, then an admin merge via branch-protection bypass — the waiver label does not clear this. |
| `waiver NOT honored` | The label is present but the gate could not confirm a write+ user applied it (self-applied, low permission, no token/API access, or the most recent label event was an `unlabeled`/a low-priv relabel). |
| `must attest the signed work, not another commit` | `ci_attested` evidence pointed at a sha other than the signed head. |
| `cwd ... rejected` | `re_executable` tried to run outside the verification worktree. |
| `policy requires request binding but the workflow provided no request source` | `require_request_binding: true` and no task file/hash reached the gate (missing `tasks/pr-N.md` on base included). |
| `not in trusted_signers.yml` | Signer's key isn't pinned on the base branch. |
| `trusted window ... expired` | The key's granted window has passed at verification time; rotate keys and re-pin. |
| `replay rejected` | Nonce already in the consumed ledger. |
| `NOT CONFIGURED` | No trust anchor on the base branch; bootstrap mode (no expiry — arm it by merging the anchor). |
| `half-configured` | `policy.yml` exists on base without `trusted_signers.yml`; fails closed instead of demoting to bootstrap. |
| `policy.yml is missing` | Trust anchor present but no policy on the base branch; an armed gate will not fall back to defaults. Commit `policy.yml`. |
| `does not explicitly set settings.require_request_binding` | Policy is under-specified; declare `require_request_binding` true/false so the control can't be disabled by omission. |
