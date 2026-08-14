# Phone → vault capture channel — design

**Created:** 2026-06-12
**Status:** approved ( 2026-06-12, after 3-question brainstorm + approach selection)
**Scope:** day-one channel: text + URLs from iPhone into `vault-personal/inbox/`

## What this is

One iOS Shortcut ("Vault It") with three entry points — Siri voice, share sheet, home-screen tap — writing raw text files into an iCloud Drive drop folder. A LaunchAgent on the (caffeinated) Mac watches the folder and feeds each arrival through the canonical `bin/vault-capture` into the vault inbox, where the existing `/ingest` → Scribe pipeline takes over.

Requirements settled in brainstorm: all gestures share one backend; queued delivery is fine (observed: seconds–1 min typical, low single-digit minutes worst case on cellular — iCloud sync is the variance source); text + URLs only on day one.

## Approaches considered

- **iCloud drop folder + watcher (CHOSEN):** zero new auth, true offline queue, one backend for all gestures, small observable Mac-side component.
- **iMessage channel (rejected day one):** fastest delivery but heaviest plumbing (Claude-in-the-loop per note, or chat.db + Full Disk Access). Addable later as a 4th gesture feeding the same folder.
- **Email-to-self + sweep (rejected day one):** solid transport but new secret management, MIME parsing, polling latency floor; overlaps Phase 2's Gmail agent.

## Architecture

```
iPhone                              iCloud              Mac (caffeinated)
┌─────────────────────────┐                      ┌──────────────────────────────┐
│ "Vault It" Shortcut      │   .txt file          │ LaunchAgent                  │
│  • Siri (dictate)        │ ──────────────────▶  │  WatchPaths + 5-min sweep    │
│  • Share sheet (URL/text)│   VaultDrop/         │     │                        │
│  • Home-screen prompt    │                      │     ▼                        │
└─────────────────────────┘                      │ bin/vault-phone-watcher      │
                                                  │   classify → vault-capture   │
                                                  │   --via phone → inbox/       │
                                                  │   stamp → _maintenance/      │
                                                  └──────────────────────────────┘
                                                        then: /ingest → Scribe → sources + stubs
```

## Components

### 1. iOS Shortcut "Vault It" (manual one-time build; exact steps)

1. New Shortcut, name **Vault It** (the name is the Siri phrase).
2. Add **Receive input from Share Sheet** (Shortcut Details → "Show in Share Sheet"; accepted types: URLs, Text, Safari web pages).
3. Action: **If** [Shortcut Input] **has any value** → set variable `note` = Shortcut Input (for Safari pages, use the page URL).
4. **Otherwise** → action **Ask for Input** (Text, prompt "Vault it:") → `note` = Provided Input. (Dictation: the mic key on the keyboard, or invoking via Siri voice drops into dictation naturally.)
5. Action: **Random Number** between 1000 and 9999.
6. Action: **Save File** — content `note`, service iCloud Drive, path `VaultDrop/`, name `capture-[Current Date, custom format yyyyMMdd-HHmmss]-[Random Number].txt`, "Ask Where to Save" OFF, "Overwrite" OFF.
7. Action: **Show Notification** "Vaulted ✓".

### 2. Drop folder

`~/Library/Mobile Documents/com~apple~CloudDocs/VaultDrop/` (iCloud Drive ▸ VaultDrop). Subdir `.failed/` for quarantine. The folder is the retry queue AND the staleness signal.

### 3. `bin/vault-phone-watcher` (new; stdlib-only Python 3, mirrors vault-capture's ethos)

Per run:
1. Acquire lockfile `VaultDrop/.lock` (`fcntl.flock`, non-blocking; exit 0 silently if held — WatchPaths and the interval sweep may collide).
2. List entries in VaultDrop, skipping `.failed/` and `.lock`. `*.icloud` placeholder entries (note: they are dot-prefixed, e.g. `.capture-x.txt.icloud` — the skip rule must special-case them) get a materialization request (`brctl download <path>`) and are left for a later run. All other dotfiles are ignored.
3. Per file, oldest first: read UTF-8 text. Empty/undecodable → move to `.failed/`. Single non-whitespace line matching `^https?://` → `vault-capture <url> --via phone`; anything else → `vault-capture - --via phone` with the text on stdin.
4. `vault-capture` exit 0 → delete the drop file (canonical copy now in inbox). Non-zero → leave the file in place (it retries every run; no attempt-cap state — the >10-min staleness alarm is the cap, turning a poison file into a visible flag rather than silent spinning).
5. Write the stamp (always, even on a 0-file run — deterministic code, never the model).

Env overrides for tests: `VAULTDROP_DIR`, `VAULT_ROOT`, `VAULT_CAPTURE_BIN`.

### 4. `vault-capture --via <label>` (small addition to the existing script)

Optional flag; default stays `vault-capture`. Front matter `via:` records provenance (`phone`). Stdin mode (`-`) and URL mode both honor it. Tested in the existing suite.

### 5. LaunchAgent `local.vault-phone-watcher.plist`

User-domain LaunchAgent: `WatchPaths` = the VaultDrop folder, `StartInterval` = 300 (sweep backup — WatchPaths can miss events; also picks up materialized placeholders), `ProgramArguments` = absolute path to the watcher script. Source lives in repo (`launchd/`), installed by copy to `~/Library/LaunchAgents/` + `launchctl bootstrap gui/$UID`.

## Observability contract (CB-12, applied at spec time)

| Point | Implementation |
|---|---|
| **Stamp** | `vault-personal/_maintenance/phone-channel-stamp.json` every run: `ran_at`, `ok`, `reason`, `processed`, `failed`, `pending`, `quarantined`, `cadence_seconds: 300`, `next_run_by` (ran_at + 600s). Output volume included so a zero-streak is visible. |
| **Alarm** | `/vault` health flags + Scout's infra check gain three checks: stamp missing or past `next_run_by` → HIGH "phone watcher not running"; any VaultDrop file older than 10 min → HIGH "phone captures stuck"; `.failed/` non-empty → MEDIUM. The reason travels with the flag. |
| **Staleness** | The stamp self-declares cadence (`next_run_by`), so consumers never hardcode the schedule. The drop folder's oldest-file age is the second, watcher-independent staleness signal. |
| **Validate-before-persist** | The watcher never writes vault content itself — `vault-capture` (tested, schema-canonical) is the only writer. Stamp written via temp-file + atomic rename. |
| **Evidence** | Failed drop files stay in VaultDrop (retry queue) or `.failed/` (quarantine); nothing is deleted on failure. |
| **Terminal surface** | `/vault` — interactive, exists, in use (verified this session: it renders Scout/queue health). Quiet when healthy: the checks add zero lines on OK. |

**Named honest gap:** if iCloud sync dies phone-side, captures never reach the Mac and no Mac-side check can see them. Day-one mitigation: none (accepted). Revisit trigger: the first capture the operator notices missing.

## Failure modes

| Failure | Behavior |
|---|---|
| iCloud slow/offline (phone) | Captures queue phone-side; sync delivers later. Invisible to Mac (named gap above). |
| Watcher dead / agent unloaded | Stamp goes stale → `/vault`/Scout HIGH flag. Drop files accumulate (nothing lost). |
| `vault-capture` fails (bad venv, disk) | File stays in VaultDrop, retried each run, >10-min flag fires. |
| Empty/binary/undecodable file | Quarantined to `.failed/`, MEDIUM flag, stamp counts it. |
| Double-fire (WatchPaths + sweep) | flock no-ops the second instance; exclusive-create in vault-capture is the backstop. |
| `.icloud` placeholder (not yet downloaded) | `brctl download` requested; picked up on a later run. |

## Testing

- TDD (CB-5): `vault-librarian/tests/test_phone_watcher.py` — classification (URL vs text vs empty), delete-on-success, keep-on-failure (fake failing capture bin via `VAULT_CAPTURE_BIN`), quarantine, stamp schema incl. zero-file runs and atomicity, lock exclusion, placeholder skip. `--via` flag cases added to `test_capture.py`.
- `plutil -lint` on the plist; live smoke: Finder-drop a file into the real VaultDrop, watch it land in inbox.
- `/rev code` before install (unattended component; per CB-8 this is project infra, not a `~/.claude` harness artifact — `/rev`, not `/gauntlet`).

## Install procedure (one sitting)

1. Mac: create `VaultDrop/` + `.failed/`; copy plist; `launchctl bootstrap gui/$UID ~/Library/LaunchAgents/local.vault-phone-watcher.plist`; verify with a Finder drop.
2. `/vault` + Scout check additions land with the same commit.
3. Phone: build the Shortcut from §Components-1 (~2 min), test all three gestures.
4. End-to-end: Siri-dictate a note, watch `/vault` show it pending, `/ingest`, `/recall` it.

## Scope cuts (day one) and future doors

- No photos/files/voice-memos (audio waits for Phase 4 Whisper; files are a Shortcut tweak + watcher passthrough later).
- No iMessage entry point (addable as a 4th gesture feeding the same folder).
- No Tailscale/SSH instant path (revisit if queue latency ever annoys).
- Company vault routing: everything lands personal; `--vault company` routing from phone is a later decision.

---

## Review-wall amendments (2026-06-12, /rev run 20260612-phone-channel)

Findings fixed before install, each pinned by a test in `tests/test_phone_watcher.py::TestHardening`:

1. **Symlinks in the drop folder are quarantined unread** (security P0 — following one would read arbitrary local files into the vault).
2. **`.icloud` placeholders count in `pending`** and both consumers' 10-minute checks include them — a capture stuck mid-sync was previously invisible to every signal.
3. **The staleness check in both consumers compares the CURRENT time to `next_run_by`** (the original wording compared `ran_at`, which is tautologically earlier — the dead-watcher alarm could never fire).
4. **The lock moved to `/tmp/vault-phone-watcher.lock`** (non-synced space; fileproviderd can swap inodes inside synced folders and flock binds to the inode).
5. **Timeouts on all subprocess calls** (60s capture via `VAULT_CAPTURE_TIMEOUT`, 30s brctl) — a hung fileproviderd no longer wedges the channel silently; brctl failures are logged and surfaced via pending.
6. **Crash-before-stamp eliminated:** per-file fault isolation (vanished files skip; rename/unlink failures count and log) plus a top-level handler that stamps `ok: false` with the crash reason.
7. **Plist runs the venv interpreter** (`vault-librarian/.venv/bin/python` — the tested Python), not system python3.
8. **CONTRACTS.md `via` enum gains `phone`**; `--via` documented in vault-capture's usage.

Deferred to open-work residuals: err.log rotation (quiet-when-healthy keeps growth slow); test-helper dedup across test files.
