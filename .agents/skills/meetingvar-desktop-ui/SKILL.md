---
name: meetingvar-desktop-ui
description: Use when reviewing or changing the meetingVAR desktop interface in this repository, especially information hierarchy, interaction states, and Windows pywebview behavior.
---

# meetingVAR desktop UI

Use this skill for changes to the meetingVAR desktop interface. The app is developed on macOS but targets Windows and pywebview/WebView2.

## Start from the checked-out version

- Check the current branch, commit, and working tree before editing. Read `desktop/README.md` and inspect the actual UI files and bridge calls on that branch.
- UI architecture changes between releases. At the time this package was prepared, `origin/main` was v0.8.0 and `origin/feat/agent-runtime` was v0.17.0; this visual refresh was implemented against desktop-shell commit `a9abea5` (v0.1.2). Treat that refresh as a design reference for newer versions, not a drop-in replacement.
- On v0.8+ preserve the chat workspace, drawers, and approval cards. Adapt only the relevant project and understanding panels; do not replace the newer app shell with the older four-page layout.
- Before changing a UI control, trace its current JavaScript handler and pywebview API call. Keep the bridge contract and existing IDs stable unless a coordinated backend change requires otherwise.

## Design and behavior

- Keep the main task and next action easy to find. Move long explanations to a small, keyboard-accessible `(i)` tooltip where useful; keep essential privacy, cost, and network disclosures visible at the point of action.
- Use a filled brand button for the main action, an outlined button for a supporting action, and a quiet text or ghost button for a trial or diagnostic action. Keep button groups aligned with a consistent gap. Use a seamless input group when an input and its read action form one control.
- Show counts and coverage as compact badges. Show failures in a persistent alert with a retry action when retrying the same operation is safe. Keep success, warning, and error states distinguishable by both wording and color.
- Keep labels and helper text readable. Use the blue and violet palette in `references/visual-direction.md` as a starting point; check contrast on the actual background and preserve Windows forced-colors behavior.
- Make states reflect actual backend results. Do not invent progress, imply a full-project read after a sample run, start model/API work just because a user navigated to a step, or bypass an approval card.
- Preserve keyboard use, visible focus, reduced-motion preferences, and useful busy/disabled states. Avoid hover-only instructions.

## Preview and validation

- For the v0.1.2 UI refresh, run `python3 desktop/preview_ui.py --port 8765` from the repository root. This browser preview injects synthetic bridge data and must not read local credentials or project files.
- Use synthetic data for UI iteration. Do not trigger real ASR, model, or interest-API requests as part of a visual preview. Never log credential values or dump the complete configuration into the page.
- Run the relevant syntax/layout checks for the files changed and report their scope. A macOS browser preview does not establish Windows WebView2 compatibility; use the checklist in `references/windows-review.md` for Windows acceptance.
- Read `docs/UI_PRE_MERGE_HANDOFF.md` when continuing this refresh or preparing its integration. Read `docs/UI_REFACTOR_0.1.2.md` for the detailed 0.1.2 implementation and synthetic-preview record. Read `docs/UI_HANDOFF_0.1.2.md` only when the checked-out code still uses that bridge contract; newer branches must be checked against their current source and docs.
