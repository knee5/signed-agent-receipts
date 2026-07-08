# receipts-verified badge

Use this badge on PRs, READMEs, and release notes when every agent-authored change in the linked artifact has a valid signed-agent-receipts JSONL receipt.

## Markdown snippet

```md
[![receipts-verified](https://img.shields.io/badge/receipts-verified-2ea44f?label=receipts&logo=github&logoColor=white)](https://knee5.github.io/signed-agent-receipts/)
```

## HTML snippet

```html
<a href="https://knee5.github.io/signed-agent-receipts/" aria-label="Verify signed agent receipts">
  <img alt="receipts: verified" src="https://img.shields.io/badge/receipts-verified-2ea44f?label=receipts&amp;logo=github&amp;logoColor=white">
</a>
```

## Rules

- Link target must be the static verifier or a repo-local verifier page.
- Badge may be used only when receipts are Ed25519-valid under `canonical-json-v1`.
- If a receipt is missing, malformed, or fails verification, use no badge or use `receipts-unverified` until fixed.
