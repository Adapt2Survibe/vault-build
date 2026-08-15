---
name: vault-surface
description: >
  Surface vault lessons or wiki pages that already cover the user's
  technical symptom. Use when the user describes a bug, gotcha, import
  death, hook failure, git weirdness, or "why is this breaking" and you
  have not /recall'd yet. Grok cannot inject hook stdout on
  UserPromptSubmit — this skill is the just-in-time path. Quiet if the
  matcher prints nothing.
---

# Vault surface

1. Run (repo root = this checkout):

```
bin/vault-prompt-match --prompt "<the user's current symptom, verbatim>"
```

2. If stdout is empty, stop. Do not invent hits.
3. If it lists LESSON or WIKI lines, `/recall` or `get_page` the ones that actually match before re-deriving.
4. Volatile lessons: re-check the `Verify:` stamp.
