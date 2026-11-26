# Carbon statusline for Claude Code

IBM Carbon Gray 100 status line on top of ccstatusline (pinned to 2.2.34).

```
┌────────────────────────────────┐ ┌──────────────────────────────────────┐
│ Opus 5.5 │ 󰉋 heimdall │  main │ │ 󰍩 Refactor the event pipeline to us… │  +128 −32 3 modified · ↑2
└────────────────────────────────┘ └──────────────────────────────────────┘
CTX █████████████████▋░░░░░░░░░░░ 42% 84k/200k │ 5H ███████▎░░░░░ 36% 2h14m │ 7D ███████████▋░░ 78% 3d10h │ CACHE 94%
```

The model, directory and branch sit in a box (`boxtop`/`boxmid`/`boxbot`). Its border uses the
app's protocol color `#3DDBD9` (`--box-color`), and its width follows the content. The last prompt sits in a second box right after it with a cyan border
(`--prompt-box-color`, `--no-prompt-box` to hide), truncated to the room left before the git
stats; below 10 cells it is dropped. Git stats are
right-aligned with the native `flex-separator`. `telemetry --fill` splits the free columns
between the CTX, 5H and 7D bars (2:1:1). `--bar-max N` caps the bars. When the bars would be
narrower than 4 cells, the row falls back to the compact tiers.
The `meter`/`meters` segments still give one row per metric if you ever want them.

## Files

| Path | Role |
|---|---|
| `~/.config/carbon-statusline/carbon.py` | Reads data, formats it and renders segments (Python 3 stdlib only) |
| `~/.config/ccstatusline/settings.json` | Layout: rows, order, flags (native ccstatusline format) |
| `ccstatusline/settings.template.json` | Layout template (`__CARBON__` = path to carbon.py) |
| `install.sh` | Installs the engine, config and `statusLine` |
| `tests/test_carbon.py` | Acceptance suite (spec §12) |

## Install

```sh
git clone <this repo> ~/.config/carbon-statusline
~/.config/carbon-statusline/install.sh
```

The script installs `ccstatusline@2.2.34` into `~/.local` and writes
`~/.config/ccstatusline/settings.json` from `ccstatusline/settings.template.json`. It points
`statusLine` in `~/.claude/settings.json` at the engine, backing up both files first, and then
runs the tests.

Requires node/npm, jq, git, python3 ≥ 3.8, a Nerd Font and truecolor (Kitty / tmux with `Tc` / Herdr).

## Engines

| Engine | `statusLine.command` | Render time |
|---|---|---|
| ccstatusline (default) | `~/.local/bin/ccstatusline` | ~500 ms (7 spawns) |
| standalone | `~/.config/carbon-statusline/carbon.py render` | ~25–45 ms |

ccstatusline's startup alone takes ~135 ms. It also runs each Custom Command inside a new
`node` → `sh` → `python` chain, about 60 ms each and one after another. So the spec's
<100 ms target can't be met with ccstatusline as the engine. A Custom Command can't output
several lines: ccstatusline measures them as one line and truncates. So each stacked
row is its own spawn. The standalone engine renders the same output
from the same code in one process. Its options go on its own command line, for example
`carbon.py render --lines "model,workspace|telemetry|prompt" --no-icons` (default: `boxtop|boxmid>gitstat|boxbot|telemetry`).

## What is native and what is custom

- **Native:** model name, separators, padding, colors, line composition, width detection.
- **Custom `workspace`:** ccstatusline's cwd widget only shows `.../name`. Its git widgets
  can't change by width, can't show `clean` vs `N modified`, and can't use U+2212. They
  also cost about 4 extra spawns. Branch, ±lines, file count and ahead/behind come from a
  single `git status --porcelain=v2 --branch` + `git diff --numstat HEAD`, cached per
  repo for `--git-ttl` seconds.
- **Custom `telemetry`:** ccstatusline has no color-by-threshold and no eighth-block bars.
- **Custom `prompt` / `rule`:** ccstatusline has no widget for these.

## Options (`commandPath` flags in ccstatusline settings)

| Flag | Meaning | Default |
|---|---|---|
| `--theme g100\|g10` | palette | g100 |
| `--order ctx,5h,7d,cache` | telemetry widgets and order (remove one to disable it) | all |
| `--bar-max N` | max bar cells in the stacked meters | 64 |
| `--detail-width N` | right-hand detail column | 30 |
| `--ctx-width N` `--5h-width N` | bar cells in the compact `telemetry` row | 10 / 5 |
| `--ctx-warn/--ctx-crit` | context thresholds | 60 / 85 |
| `--5h-warn/--5h-crit`, `--7d-…` | usage thresholds | 70 / 90 |
| `--no-reset` / `--reset` | meters: hide reset details. telemetry row: hide 5H / show 7D countdown | shown |
| `--scope auto\|session\|request` | cache metric. `auto` falls back to the last request and labels it `CACHE·REQ` | auto |
| `--max N` | prompt length cap (cells) | width |
| `--prompt-box-color T` / `--no-prompt-box` | prompt box border color token / hide it | cyan / shown |
| `--no-icons` | drop Nerd Font glyphs | icons on |
| `--git-ttl S` | git cache seconds | 2 |
| `--no-git-detail` | branch only | off |

The `rule` and standalone `prompt` segments still exist; add them back as rows if you want them.

## Responsive tiers (usable width = terminal − 7)

- **≥100:** everything.
- **70–99:** `3M` instead of `3 modified`, context bar shrinks to 5 cells, no 5H bar.
- **<70:** no git detail, no bars, no countdown, no cache. Model, CTX, 5H/7D, dir and prompt stay.

## Guarantees

- Missing fields show `—` (CTX) or are hidden (5H/7D/CACHE). They are never shown as 0%.
- There is no 200k assumption and no starting estimate. The fallback is
  `(input + cache_read + cache_creation) / context_window_size`.
- The transcript is read backwards in 256 KB → 8 MB chunks. The result is cached by
  session id and size+mtime.
- No network calls, no `git fetch`, no credential reads. Git runs with a 0.5 s timeout
  and `--no-optional-locks`.
- User text is cleaned of escape sequences (CSI, OSC, C0/C1) and bidi/format characters.
  Truncation counts terminal cells.
- Cache files live in `$XDG_RUNTIME_DIR/carbon-statusline/` (mode 0700), with one key per
  session or repo.

## Rollback

`~/.claude/settings.json.bak-statusline-*` holds the previous setting
(`~/.claude/scripts/context-bar.sh`).
