Demo task, dispatched for the PR that lands as #5.

Add a "verify a receipt without trusting the author" section to README.md:
the one-command local verification (`signed-agent-receipts verify-receipt`),
a link to the pinned canonical-diff spec in docs/RECEIPTS-GATE.md for
auditors who want to recompute the diff hash with bare git, and the honesty
boundary from SECURITY-MODEL.md (unaltered record + which key signed it, not
"the work is good"). Docs-only change; touch nothing outside README.md.

Open it as a clearly-labeled demo PR and emit a v0.2 receipt bound to this
file's exact bytes (sign-then-attach). This PR is one half of the public
receipts-gate demonstration described in docs/DEMO.md; its twin carries no
receipt and must fail the same check.
