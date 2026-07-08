Demo task, dispatched for the PR that lands as #8.

Fix the v0.2 install gap in INSTALL-FOR-AGENTS.md: the one-line agent
installer only offers the PyPI v0.1.x package, but the gate commands
(receipt / verify-receipt / gate / consume) are git-only until v0.2 ships to
PyPI. Add the git-install line, matching what AGENTS.md already says.
Docs-only change; touch nothing outside INSTALL-FOR-AGENTS.md.

Then demonstrate tamper-evidence on this same PR: emit and attach a valid
v0.2 receipt for the work head (sign-then-attach), and afterwards push one
more work commit on top of the attached receipt. The receipts-gate must
refuse: after the signed head, only receipts/** may change, so the delivered
head no longer matches what was signed. (A force-push after signing is the
sibling case and draws the STALE refusal instead.) This PR is the third
exhibit of the public receipts-gate demonstration described in docs/DEMO.md;
it stays open, and red, on purpose.
