# AGENTS.md — meetingVAR

This file is for AI coding assistants working in this repository. Read it before changing anything.

## What this project is

meetingVAR is a **local-first meeting evidence agent** for Windows, not a meeting-notes app.

It captures system audio + microphone + screen only when content matches the user's project interests, then produces:

- a replayable clip (audio + screen frames + transcript);
- an Evidence Pack (ZIP + manifest + SHA256);
- a Commitment Ledger (who promised what, who changed it);
- a Find Moment result (the exact 30–90 second segment behind a question).

Positioning: **“别人帮你记下来，meetingVAR 帮你证出来。”**

Competition: SDG Open Hack 2026 · Hack16 · Lenovo track. Do not reposition it as a generic AI meeting assistant.

## Read first

1. `docs/TEAM_ONBOARDING_2026SDG.md` — full handover, roles, architecture, red lines.
2. `docs/FINAL_SPRINT_5MIN.md` — current demo plan and P0 tasks.
3. `desktop/README.md` — pywebview shell and bridge details.
4. `docs/EVIDENCE_SKILLS_0.17.0.md` — the three evidence skills.
5. `docs/DEVELOPMENT_STATUS.md` — environment, build, release history.

## Determine your role before editing

If the user has not stated your role, ask. Default boundaries:

| Role | May edit | Must not edit |
|---|---|---|
| UI | `desktop/web/**` | `src/**`, `desktop/app.py`, build/release files |
| QA / demo | `scripts/**`, `tests/**`, `docs/**` | `src/**`, `desktop/web/**`, `desktop/app.py` |
| Core | `src/**`, `desktop/app.py`, build files | `data/**`, release artifacts, secrets |

If a task crosses these boundaries, stop and ask the project owner.

## Hard red lines

- Never read, print, copy, or commit secrets:
  - `%APPDATA%\meetingVAR\ui_config.json` (contains API key)
  - `.env`, `meetingvar.env.enc`
  - `C:\Users\Lenovo\OneDrive\Desktop\keys\**`
- Never commit, upload, or delete `data/`. It contains real meeting audio, screen frames, and transcripts.
- Never edit or delete `<project>/meetingvar/agent_memory/` or `evidence_packs/` without explicit permission.
- Never change version numbers, tags, `dist/`, `dist_installer/`, or release folders.
- Never run `git reset --hard`, `git clean -fd`, or delete files outside your assigned area.
- Never rewrite the core to “fix” a UI issue.
- Never add a frontend framework or build chain. The UI is plain HTML/CSS/JS.
- Never fabricate metrics. No human transcript → no CER/WER claim. No human labels → no entity/relation P/R claim. No real users → no satisfaction claim.

## Environment

| Resource | Path |
|---|---|
| Repo | `C:\Users\Lenovo\projects\meetingVAR` |
| Engine Python | `D:\miniconda3\envs\meetingvar\python.exe` |
| UI Python | `D:\env\ui-venv\python.exe` |
| Local models | `D:\env\models\models` |
| Installed app | `C:\Users\Lenovo\AppData\Local\Programs\meetingVAR` |

Python must be 3.10.x (`>=3.10,<3.11`). Do not upgrade to 3.13; `audioop` was removed.

### Commands

Run UI from source:

```bat
D:\env\ui-venv\python.exe desktop\app.py
```

Check engine:

```bat
D:\miniconda3\envs\meetingvar\python.exe desktop\engine_bridge.py check
```

Run tests:

```bat
D:\miniconda3\envs\meetingvar\python.exe -m unittest discover -s tests -v
```

Current baseline: 543 tests pass, ~72 s. Do not run packaging/build unless the owner asks.

## Architecture in one screen

```text
audio/screen capture → live pipeline → clips/pool
                              ↓
                    ASR / speaker / trigger
                              ↓
              RAG + project memory + knowledge graph
                              ↓
                 ChatAgent / AgentLoop / skills
                              ↓
                 desktop/app.py (pywebview bridge)
                              ↓
                 desktop/web (HTML/CSS/JS)
```

- The desktop shell does **not** import engine internals.
- It launches `python -m meetingvar go/record ...` as a subprocess.
- Engine events arrive on stdout as `@@MEETINGVAR_EVENT`; stdin accepts `:stop` and `:var`.
- Frontend calls `window.pywebview.api.*`; wait for `pywebviewready`.
- pywebview is pinned to 5.4. Do not upgrade it casually.

## Current P0 work

The backend for evidence skills exists; the UI is missing. Priority:

1. **Evidence Pack viewer/verifier**
   - show ZIP/manifest, audio, transcript, screen frames, file SHA256;
   - one-click verification;
   - offline.
2. **Commitment Ledger table**
   - `msg.ledger` is returned by the backend but not rendered by `desktop/web/app.js`;
   - show owner/due/status/diff and a “original quote” jump.
3. **Find Moment cards**
   - `msg.segments` is returned but not rendered;
   - show time, speaker, quote, why, play button.
4. **HUD / second screen** (P1)
   - 8-inch readable status card;
   - works on any Windows second monitor;
   - Magic Bay may expose a standard UVC camera and/or a standard Windows display; integrate via OS device classes, never via host-private protocol.

Current known gaps:

- `desktop/web/app.js` only renders message text, citations, and actions; it ignores `ledger` and `segments`.
- Evidence Pack action currently returns only a ZIP path string.
- `manifest.consent` is hardcoded to `local-capture`; not a real consent record.
- Installed app may show “未配置引擎 Python”; set engine path and repo path in Settings → General.
- Magic Bay is not tested on real hardware. Do not claim “integrated with Magic Bay”; say “Magic Bay-ready”.

## UI rules

- Dark theme; low-saturation; readable at 1280×800 and 1920×1080.
- Chinese UI copy, consistent with existing wording.
- No CDN; everything must work offline.
- Reuse existing CSS variables/components in `desktop/web/style.css`.
- Do not add a frontend framework.
- Mock data goes in a separate mock/dev path, never into production data flow.

## QA / demo rules

- Every “passed” claim needs command, environment, output summary, and artifact path.
- Test matrix: online/offline, one/two screens, mic/no mic, local/cloud ASR, zero-hit/hit, evidence pack verify, ledger diff, find-moment timestamp.
- Demo data must be repeatable. If live capture does not produce a clip, use manual VAR or a pre-recorded file.
- Keep a 5-minute fallback video.

## Git / PR rules

- Do not push to `main`.
- Branch from `feat/agent-runtime`.
- One PR = one concern.
- Do not commit `data/`, models, installers, secrets, or `ui_config.json`.
- Report changes as: files changed, why, test command, result, screenshot/log path.

## Reporting format

```text
Role:
Files changed:
Behavior:
Test command:
Result:
Artifacts:
Open questions:
```

If you cannot verify something, say “not verified”. Do not turn “not verified” into “done”.