# Working on OpenChat Toolkit

Read README.md and docs/ARCHITECTURE.md before editing. This is a Windows local app with real message-sending capability.

- Run `python audit_release.py` and `python run_tests.py`. The latter creates a separate synthetic workspace; do not run fixture tests against user state.
- Initial rooms are empty, collection is disabled, and posting is paused. Setup must not register tasks or send messages.
- Changes must stay within this repository. Do not import another installed application's config, history, credentials, database, task names or helper binaries.
- Preserve exact room/window/draft binding, pre-invocation durable intent, optimistic versions, confirmation tokens and process locks.
- Never retry uncertain sends automatically or rewrite their database state to pending/sent. Surface needs_review and let the owner reconcile.
- Before live tests, use the owner's explicit destination, text and count authorization. Existing explicit authorization is sufficient within that exact scope. Never substitute a production room for a test room.
- Keep private messages, media, logs, keys, session tokens and runtime state out of Git, prompts and diagnostic output. Use synthetic fixtures.
- Never weaken endpoint protection or session/desktop safety checks. A successful local bubble check is not recipient delivery confirmation.
- Keep Python, C# and UI delivery-window logic aligned. Preserve old archived records when new exports contain fewer messages.
- `release-files.json` is the publication allowlist. Review each added file. Do not publish this repository or switch production without the owner's approval.
