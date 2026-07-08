# receipts-gate: install and operate

The gate makes "this PR carries a verifiable receipt" a required CI check.
It verifies signature, trusted signer, key windows, and revocations; it
independently recomputes the PR diff hash; it verifies typed evidence; and it
applies your acceptance policy. Read [SECURITY-MODEL.md](../SECURITY-MODEL.md)
first — it says exactly what this does and does not prove.

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

jobs:
  receipts-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0          # the gate recomputes diffs; it needs history
      - uses: knee5/signed-agent-receipts@main   # pin a tag or SHA in production
        with:
          github-token: ${{ github.token }}
```

`labeled`/`unlabeled` matter: applying the waiver label re-runs the gate
instead of leaving a stale failing check.

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

Until `trusted_signers.yml` exists on the base branch, the gate runs in
**bootstrap mode**: it reports receipts and passes with a loud
"NOT CONFIGURED" notice, so adopting the workflow never bricks the repo.
Merging the trust anchor arms it.

## 3. Lock the config down (repo settings — required)

The gate reads both config files ONLY from the PR's base branch and fails
any PR touching `.agent-receipts/**`. For that to mean anything:

1. **Branch protection** on your default branch, with `receipts-gate` as a
   **required status check**.
2. **CODEOWNERS**: add `/.agent-receipts/ @your-maintainers` and require
   code-owner review in branch protection.

Config changes then land only via maintainer-reviewed PRs, merged with the
waiver label (the gate's own failure on config PRs is by design).

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
`re_executable_allowlist`. `self_claimed` is displayed, never counted.

## 5. Request binding (optional, recommended for dispatched work)

If the task the agent worked from is committed on the base branch (say
`tasks/refactor-auth.md`), tell the gate:

```yaml
      - uses: knee5/signed-agent-receipts@main
        with:
          request-source-file: tasks/refactor-auth.md
```

The gate then requires `request.hash` to equal the SHA-256 of that file's
bytes on the base branch. Alternatively pass `request-hash` directly. Without
either input, the hash is reported but unchecked — see SECURITY-MODEL.md.

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
| `PR modifies gate configuration` | `.agent-receipts/**` changed. Maintainer review + waiver label. |
| `not in trusted_signers.yml` | Signer's key isn't pinned on the base branch. |
| `replay rejected` | Nonce already in the consumed ledger. |
| `NOT CONFIGURED` | No trust anchor on the base branch; bootstrap mode. |
