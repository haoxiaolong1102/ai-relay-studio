# ai-relay

**English** | [简体中文](README.zh-CN.md)

Pass one project between Claude Code, Codex and Antigravity like a relay baton — so when one subscription runs out of quota, another keeps working on the same folder, and you can always see who changed what and get back to where you were.

A small macOS menu-bar item plus a Python command (`bin/relay`). Standard library only, no server, no API keys: it drives the official command-line tools you are already signed in to.

> Status: a personal tool, published as-is (v0.1). It works on the author's Mac; read [Limits](#limits) and [What has been tested](#what-has-been-tested) before trusting it with real work.

## Why

Each AI coding subscription has its own quota window. When Claude stops answering mid-task, the usual options are to wait or to re-explain the whole project to another tool and hope the two don't overwrite each other. ai-relay turns that hand-over into one click:

- **One writer at a time.** A lock says who holds the project. While Codex or Antigravity holds it, Claude Code is limited to read-only in that project by a hook.
- **Nothing is lost.** A snapshot is taken before every hand-off, and a per-file manifest shows exactly what changed while the other tool had it.
- **The next tool knows what to do.** A baton file (`docs/relay/BATON.md`) carries the project state forward; the tool writes back a hand-back note (`docs/relay/HANDBACK.md`).
- **Git stays clean.** The other tool's work is recorded on its own branch, `relay/<tool>/<timestamp>`, authored as that tool. Your current branch, index and working tree are never touched.

## What it does

| You want to… | Menu item | Command |
|---|---|---|
| Let Codex continue unattended | Hand off to Codex | `relay handoff --project X` |
| Let Antigravity continue unattended | Hand off to Antigravity | `relay handoff --project X --to antigravity` |
| Work in Antigravity yourself (snapshot + lock only, no model started) | Work in Antigravity myself | `relay open --project X --tool antigravity` |
| Give the project back to Claude | Done / Give back to Claude | `relay takeback --project X` |
| See who holds what | Detailed status… | `relay status` |
| Turn everything off, repair, view logs | Master switch / More… | `relay switch` |
| Look at the project as it was before the hand-off | — | `relay restore X` (extracts to a new folder) |

An unattended hand-off runs in segments (by default up to 4 segments of 20 minutes, 90 minutes in total). After each segment the relay diffs the files, runs your project's test command and decides whether to continue. It stops on its own, with a plain-language reason, when the task is done, quota runs out, an action was denied, two segments made no progress, or something outside the allowed area was touched.

## How it works

```
Claude Code ──hand off──▶ baton + snapshot + manifest ──▶ lock: codex / antigravity
     ▲                                                          │
     │                                              tool works in segments
     │                                                          ▼
     └──take back── manifest diff + isolated branch ◀── HANDBACK.md
```

- **Lock** — `state/locks/<project>.json`, mirrored to `~/.claude/relay-holders.json` so the guard still works when the relay folder sits on an unplugged external drive.
- **Guard** — a Claude Code `PreToolUse` hook. If Claude does not hold the project, writes are refused; if the check itself fails, it also refuses (fail closed).
- **Snapshot** — `git` archive for Git projects, `rsync --link-dest` for plain folders.
- **Manifest** — per-file hashes before and after. The diff is the source of truth; whatever the tool *says* it did is treated as untrusted text.
- **Isolated branch** — built with a temporary index and `git commit-tree`, then `git push <remote> <ref>:<ref>` (never forced). Changes that were already uncommitted at hand-off go into a separate preceding commit, so they are not attributed to the tool. Registered raw/asset folders and large new files are left out.
- **Audit log** — every hand-off, snapshot, branch and push is written to `state/audit/`, with secrets redacted.

## Requirements

- macOS 13 or later, with the system Python (`/usr/bin/python3`, 3.9+). No packages to install.
- [Claude Code](https://claude.com/claude-code) as the main tool (hooks and skills are used).
- At least one of: the Codex CLI (ships inside the ChatGPT desktop app) or the Antigravity CLI (`agy`), signed in with your own account.
- Xcode Command Line Tools if you want the menu-bar item (`swiftc`). The command line works without it.

## Install

```bash
git clone https://github.com/fatrabbit29/ai-relay.git
cd ai-relay
sh install.sh
```

`install.sh` copies the example config, installs the hook shim to `~/.claude/relay-shim.sh`, installs two Claude Code skills, and builds `~/Applications/Relay.app`. It backs up anything it would overwrite and never edits a `settings.json`. Then:

1. Edit `config/projects.json`: register your projects (absolute paths), set model names your plans actually offer, and choose permissions.
2. For each project where Claude should be guarded, merge the `hooks` block from `config/claude-hooks.json` into that project's `.claude/settings.json`.
3. Give each project a `docs/STATE.md` with a "下一步" (next steps) list. The first unfinished item is what the other tool will work on, so make it concrete.
4. Run `/usr/bin/python3 bin/relay status`, then `open ~/Applications/Relay.app`.

Self-tests (no model is called): `/usr/bin/python3 -m unittest discover -s tests -q`

## Permissions and safety

The example config is the conservative setting. You can widen it; understand what you give up.

| Setting | Conservative (default in the example) | Wide open |
|---|---|---|
| `codex.sandbox` | `workspace-write` — writes only inside the project | `danger-full-access` — no sandbox |
| `codex.network_access` | `false` | `true` |
| `antigravity.permissions` | `edits` — may edit files in the project, cannot run commands | `full` — skips every confirmation, whole machine |
| `git.push` | `false` — branch stays local | `true` — pushes the isolated branch only |

With the wide-open settings, the remaining protection is *detect and recover* (snapshot, manifest, isolated branch), not *prevent*. The fixed instructions in `config/codex_prompt.md` and `config/agy_prompt.md` (do not commit, do not delete, do not touch raw assets, do not upload code or data) are instructions to a model, not enforcement.

## Limits

- The guard only covers Claude Code in projects where the hook is installed. Other apps, or you, editing the same folder are noticed afterwards by the manifest diff, not blocked.
- Read-only protection of raw/asset folders is a speed bump: a process running as you can undo it.
- Snapshots live on whatever disk you configure. If that is the same disk as the project, they fail together.
- The menu follows the system language (Chinese or English). **Terminal output, dialogs and the built-in agent prompts are in Chinese only for now.**
- Quota-exhaustion detection for Antigravity is keyword-based guesswork.
- `bin/media_ask` (let Gemini listen to or watch a media file) is optional and its video route depends on a private helper that is not included.
- Model names in the example config are placeholders. Use ones your plan offers.

## What has been tested

- 113 automated tests with fake Codex/Antigravity executables: locking, guard, snapshots, manifests, hand-off flow, isolated branches, push failure handling.
- Real runs on the author's machine: unattended hand-off to Codex and to Antigravity (`agy` 1.5.7) on small test projects, take-back, isolated branch pushed to a local bare remote.
- **Not yet tested for real:** pushing to GitHub itself, multi-segment runs, real quota exhaustion, large repositories, any machine other than the author's.

## Terms of service

ai-relay only launches each vendor's official command-line tool under your own signed-in account, one at a time, the way you could by hand. It does not share accounts, proxy requests or bypass limits. You are responsible for checking that unattended use fits the terms of the plans you pay for. This project is not affiliated with Anthropic, OpenAI or Google.

## Uninstall

Quit the menu item, then remove `~/Applications/Relay.app`, `~/.claude/relay-shim.sh`, `~/.claude/skills/relay-handoff`, `~/.claude/skills/relay-takeback`, `~/.claude/relay-holders.json`, the `hooks` block you merged into each project, and this folder. If you protected raw folders, run `relay unprotect <project>` first.

## License

MIT — see [LICENSE](LICENSE). Provided without warranty; keep your own backups.
