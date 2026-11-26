#!/usr/bin/env -S python3 -I -S
"""Carbon statusline segments for ccstatusline Custom Command widgets.

Usage (inside a ccstatusline `commandPath`):
    carbon.py <segment> [options]

Segments:
    render     all rows in one process (standalone mode, no ccstatusline)
               --lines "boxtop|boxmid>gitstat|boxbot|telemetry"
    workspace  "󰉋 heimdall │  main +128 −32 3 modified · ↑2"
    telemetry  "CTX ████░░░░░░ 42% │ 5H ██░░░ 36% 2h14m │ 7D 78% │ CACHE 94%"
               --order ctx,5h,7d,cache   per-metric: --ctx-width --5h-warn ...
    model dir git ctx 5h 7d cache     individual pieces
    prompt     "󰍩 last user instruction…"
    rule       full-width thin horizontal rule

Common options (all configured in ccstatusline's settings.json commandPath):
    --theme g100|g10      palette (default g100)
    --no-icons            drop Nerd Font glyphs
    --width N             progress-bar width (ctx/5h)
    --warn N --crit N     thresholds (percent)
    --no-reset            hide 5h countdown
    --reset               show 7d countdown
    --scope auto|session|request   cache metric scope (default auto)
    --max N               max prompt length in cells
    --git-ttl SECONDS     git cache duration (default 2)

Data contract: Claude Code status JSON on stdin (+ `terminal_width` injected by
ccstatusline). Missing fields are *unavailable*, never zero. The script never
touches the network, never runs `git fetch` and never reads credentials.
"""

import json
import os
import re
import sys
import time
import unicodedata

# ───────────────────────────── design tokens ──────────────────────────────

THEMES = {
    "g100": {
        "separator": "#393939", "text": "#F4F4F4", "text2": "#C6C6C6",
        "muted": "#A8A8A8", "accent": "#BE95FF", "info": "#78A9FF",
        "success": "#42BE65", "warning": "#F1C21B", "critical": "#FA4D56",
        "cyan": "#3DDBD9", "protocol": "#3DDBD9",
    },
    "g10": {
        "separator": "#C6C6C6", "text": "#161616", "text2": "#393939",
        "muted": "#6F6F6F", "accent": "#8A3FFC", "info": "#0F62FE",
        "success": "#198038", "warning": "#B28600", "critical": "#DA1E28",
        "cyan": "#007D79", "protocol": "#007D79",
    },
}

ICONS = {"dir": "\U000f024b", "branch": "", "prompt": "\U000f0369"}

FULL, EMPTY = "█", "░"
EIGHTHS = ["", "▏", "▎", "▍", "▌", "▋", "▊", "▉"]


def opts(argv):
    o = {"_": []}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            key = a[2:]
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                o[key] = argv[i + 1]
                i += 2
                continue
            o[key] = True
        else:
            o["_"].append(a)
        i += 1
    return o


def num(o, key, default):
    try:
        return float(o.get(key, default))
    except (TypeError, ValueError):
        return default


class Paint:
    def __init__(self, theme):
        self.t = THEMES.get(theme, THEMES["g100"])

    def __call__(self, token, text):
        h = self.t.get(token, token).lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"\x1b[38;2;{r};{g};{b}m{text}\x1b[39m"


# ─────────────────────────────── helpers ─────────────────────────────────

CTRL_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-_]|[\x00-\x1f\x7f-\x9f]")
WS_RE = re.compile(r"\s+")


def sanitize(text):
    """Strip ANSI/OSC sequences and C0/C1 controls, collapse whitespace."""
    text = CTRL_RE.sub(" ", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Co")
                   or ch in ("‍",))
    return WS_RE.sub(" ", text).strip()


def cell_width(ch):
    if unicodedata.combining(ch) or ch == "‍" or unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    return 1


def truncate(text, cells):
    if cells <= 0:
        return ""
    if sum(cell_width(c) for c in text) <= cells:
        return text
    out, used = [], 0
    for ch in text:
        w = cell_width(ch)
        if used + w > cells - 1:
            break
        out.append(ch)
        used += w
    return "".join(out).rstrip() + "…"


def get(d, *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def as_pct(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return max(0.0, min(100.0, float(v)))


def bar(pct, width, color, paint):
    width = max(1, int(width))
    filled = pct / 100.0 * width
    full = int(filled)
    frac = EIGHTHS[int(round((filled - full) * 8)) % 8] if full < width else ""
    if full < width and round((filled - full) * 8) == 8:
        full, frac = full + 1, ""
    empty = width - full - (1 if frac else 0)
    s = paint(color, FULL * full + frac) if (full or frac) else ""
    return s + paint("separator", EMPTY * max(0, empty))


def level(pct, warn, crit, ok, warn_c="warning", crit_c="critical"):
    if pct >= crit:
        return crit_c
    if pct >= warn:
        return warn_c
    return ok


def countdown(resets_at):
    if resets_at is None:
        return None
    try:
        if isinstance(resets_at, (int, float)):
            ts = float(resets_at)
            if ts > 1e12:
                ts /= 1000.0
        else:
            from datetime import datetime
            ts = datetime.fromisoformat(str(resets_at).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, OverflowError):
        return None
    secs = int(ts - time.time())
    if secs < 60:
        return "<1m"
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}d{h}h"
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m"


def tier(width):
    if width >= 100:
        return "wide"
    if width >= 70:
        return "mid"
    return "narrow"


def cache_dir():
    base = os.environ.get("XDG_RUNTIME_DIR") or os.path.join("/tmp", f"carbon-statusline-{os.getuid()}")
    path = os.path.join(base, "carbon-statusline")
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def safe_key(s):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(s))[:120]


def cache_read(name, valid):
    try:
        with open(os.path.join(cache_dir(), name), encoding="utf-8") as f:
            data = json.load(f)
        return data if valid(data) else None
    except (OSError, ValueError):
        return None


def cache_write(name, data):
    try:
        d = cache_dir()
        tmp = os.path.join(d, f".{name}.{os.getpid()}")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, os.path.join(d, name))
    except OSError:
        pass


# ─────────────────────────────── segments ────────────────────────────────

def git_cached(data, o):
    cwd = get(data, "workspace", "current_dir") or data.get("cwd")
    if not isinstance(cwd, str) or not os.path.isdir(cwd):
        return None
    ttl = num(o, "git-ttl", 2)
    key = f"git-{safe_key(os.path.realpath(cwd))}.json"
    now = time.time()
    info = cache_read(key, lambda c: 0 <= now - c.get("at", 0) < ttl)
    if info is None:
        info = git_info(cwd)
        info["at"] = now
        cache_write(key, info)
    return info


def git_detail(info, o, p, width):
    t = tier(width)
    if t == "narrow" or o.get("no-git-detail"):
        return ""
    parts = []
    ins, dels = info.get("ins", 0), info.get("del", 0)
    if ins:
        parts.append(p("success", f"+{ins}"))
    if dels:
        parts.append(p("critical", f"\u2212{dels}"))
    files = info.get("files", 0)
    if t == "wide":
        parts.append(p("text2", f"{files} modified") if files else p("muted", "clean"))
    else:
        parts.append(p("text2", f"{files}M") if files else p("muted", "clean"))
    sync = ""
    if info.get("ahead"):
        sync += f"\u2191{info['ahead']}"
    if info.get("behind"):
        sync += f"\u2193{info['behind']}"
    out = " ".join(parts)
    if sync:
        out += p("muted", " \u00b7 ") + p("text2", sync)
    return out


def seg_git(data, o, p, width):
    info = git_cached(data, o)
    if not info or not info.get("repo"):
        return ""
    return " ".join(x for x in (seg_branch(data, o, p, width, info), git_detail(info, o, p, width)) if x)


def git_info(cwd):
    import subprocess

    def run(*args):
        try:
            r = subprocess.run(["git", "--no-optional-locks", "-C", cwd, *args],
                               capture_output=True, text=True, timeout=0.5,
                               env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"})
            return r.stdout if r.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None

    st = run("status", "--porcelain=v2", "--branch", "--untracked-files=normal")
    if st is None:
        return {"repo": False}
    info = {"repo": True, "files": 0, "ahead": 0, "behind": 0, "ins": 0, "del": 0}
    has_head = True
    for line in st.splitlines():
        if line.startswith("# branch.head "):
            info["branch"] = line[14:]
        elif line.startswith("# branch.oid ") and not line.endswith("(initial)"):
            info["oid"] = line[13:20]
        elif line.startswith("# branch.ab "):
            a, b = line.split()[2:4]
            info["ahead"], info["behind"] = int(a.lstrip("+")), abs(int(b))
        elif line.startswith("# branch.oid (initial)"):
            has_head = False
        elif line[:1] in ("1", "2", "u", "?"):
            info["files"] += 1
    diff = run("diff", "--numstat", "HEAD") if has_head else run("diff", "--numstat", "--cached")
    for line in (diff or "").splitlines():
        a, d, *_ = line.split("\t") + ["", ""]
        if a.isdigit():
            info["ins"] += int(a)
        if d.isdigit():
            info["del"] += int(d)
    return info


def ctx_pct(data):
    cw = data.get("context_window")
    if not isinstance(cw, dict):
        return None
    pct = as_pct(cw.get("used_percentage"))
    if pct is not None:
        return pct
    size = cw.get("context_window_size")
    usage = cw.get("current_usage")
    if not isinstance(size, (int, float)) or size <= 0 or not isinstance(usage, dict):
        return None
    toks = [usage.get(k) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")]
    if all(not isinstance(v, (int, float)) for v in toks):
        return None
    return as_pct(sum(v for v in toks if isinstance(v, (int, float))) / size * 100)


def seg_ctx(data, o, p, width):
    t = tier(width)
    pct = ctx_pct(data)
    label = p("muted", "CTX")
    if pct is None:
        return f"{label} {p('separator', EMPTY * 10) + ' ' if t == 'wide' else ''}{p('muted', '—')}"
    color = level(pct, num(o, "warn", 60), num(o, "crit", 85), "success")
    w = num(o, "width", 10) if t == "wide" else (5 if t == "mid" else 0)
    b = bar(pct, w, color, p) + " " if w else ""
    return f"{label} {b}{p(color, f'{pct:.0f}%')}"


def seg_limit(data, o, p, width, bucket, label, default_width, default_reset):
    t = tier(width)
    rl = get(data, "rate_limits", bucket)
    pct = as_pct(rl.get("used_percentage")) if isinstance(rl, dict) else None
    lab = p("muted", label)
    if pct is None:
        return ""  # unavailable (API key / non-subscription session): hide, never show 0%
    color = level(pct, num(o, "warn", 70), num(o, "crit", 90), "info")
    w = num(o, "width", default_width) if t == "wide" else 0
    out = f"{lab} " + (bar(pct, w, color, p) + " " if w else "") + p(color, f"{pct:.0f}%")
    show_reset = (default_reset and not o.get("no-reset")) or o.get("reset")
    if show_reset and t != "narrow":
        cd = countdown(rl.get("resets_at"))
        if cd:
            out += " " + p("text2", cd)
    return out


def cache_value(data, o):
    """-> (pct, 'session'|'request') or None."""
    scope = o.get("scope", "auto")
    if scope in ("auto", "session"):
        r = get(data, "prompt_cache", "hit_ratio")
        if isinstance(r, (int, float)) and not isinstance(r, bool):
            return as_pct(r * 100 if r <= 1 else r), "session"
    if scope in ("auto", "request"):
        u = get(data, "context_window", "current_usage")
        if isinstance(u, dict):
            vals = {k: u.get(k) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}
            if all(isinstance(v, (int, float)) for v in vals.values()):
                den = sum(vals.values())
                if den > 0:
                    return as_pct(vals["cache_read_input_tokens"] / den * 100), "request"
    return None


def seg_cache(data, o, p, width):
    if tier(width) == "narrow":
        return ""
    out = cache_value(data, o)
    if out is None:
        return ""
    pct, scope = out
    label = "CACHE·REQ" if scope == "request" and o.get("scope", "auto") == "auto" else "CACHE"
    color = "success" if pct >= 80 else ("warning" if pct >= 50 else "muted")
    return f"{p('muted', label)} {p(color, f'{pct:.0f}%')}"


SKIP_PREFIXES = ("[Request interrupted", "[Request cancelled", "Caveat:", "<command-", "<local-command-",
                 "<system-reminder", "<user-prompt-submit-hook", "<bash-", "<task-notification")


def message_text(entry):
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isSidechain") \
            or entry.get("isCompactSummary") or entry.get("toolUseResult") is not None:
        return None
    msg = entry.get("message")
    if not isinstance(msg, dict) or msg.get("role", "user") != "user":
        return None
    content = msg.get("content")
    if isinstance(content, str):
        texts = [content]
    elif isinstance(content, list):
        if any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
            return None
        texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
    else:
        return None
    kept = []
    for t in texts:
        if not isinstance(t, str):
            continue
        s = re.sub(r"<system-reminder>.*?</system-reminder>", " ", t, flags=re.S)
        s = re.sub(r"<pasted_content\b[^>]*>.*?(?:</pasted_content[^>]*>|$)", " [paste] ", s, flags=re.S)
        s = re.sub(r"\[Image #\d+\]", " ", s)
        s = re.sub(r"\[Pasted text #\d+[^\]]*\]", " [paste] ", s).strip()
        if not s or s.startswith(SKIP_PREFIXES):
            continue
        kept.append(s)
    text = sanitize(" ".join(kept))
    return text or None


def last_prompt(path, session):
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = f"prompt-{safe_key(session or path)}.json"
    sig = [st.st_size, st.st_mtime_ns]
    hit = cache_read(key, lambda c: c.get("sig") == sig and c.get("path") == path)
    if hit is not None:
        return hit.get("text")
    text = None
    try:
        with open(path, "rb") as f:
            chunk, limit = 256 * 1024, 8 * 1024 * 1024  # bounded reverse scan
            read = 0
            while text is None and read < min(st.st_size, limit):
                read = min(st.st_size, limit, max(chunk, read * 2))
                f.seek(st.st_size - read)
                buf = f.read(read)
                lines = buf.split(b"\n")
                if read < st.st_size:
                    lines = lines[1:]  # first line is probably partial
                for raw in reversed(lines):
                    if b'"user"' not in raw:
                        continue
                    try:
                        entry = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(entry, dict):
                        text = message_text(entry)
                        if text:
                            break
    except OSError:
        return None
    cache_write(key, {"sig": sig, "path": path, "text": text})
    return text


def seg_prompt(data, o, p, width):
    path = data.get("transcript_path")
    if not isinstance(path, str) or not path:
        return ""
    text = last_prompt(path, data.get("session_id"))
    if not text:
        return ""
    icon = "" if o.get("no-icons") else ICONS["prompt"] + " "
    room = width - (2 if icon else 0)
    if "max" in o:
        room = min(room, int(num(o, "max", room)))
    body = truncate(text, room)
    chip = paste_chip(p, o.get("paste-color", "protocol")) if o.get("paste-box") else p("text2", "[paste]")
    color = o.get("text-color", "text2")
    out = "".join(chip if i % 2 else p(color, part)
                  for i, part in enumerate(re.split(r"(\[paste\])", body)) if part)
    return (p(o.get("icon-color", "info"), icon) if icon else "") + out


def paste_chip(p, token):
    """'[paste]' as a 7-cell bordered box: thin side blocks + overline/underline in the token color."""
    rgb = p.t.get(token, token).lstrip("#")
    r, g, b = int(rgb[0:2], 16), int(rgb[2:4], 16), int(rgb[4:6], 16)
    return (p(token, "\u2595") + f"\x1b[53;4;58;2;{r};{g};{b}m" + p(token, "paste")
            + "\x1b[55;24;59m" + p(token, "\u258f"))


def seg_rule(data, o, p, width):
    return p("separator", "─" * width)


def seg_model(data, o, p, width):
    m = data.get("model")
    name = (m.get("display_name") or m.get("id")) if isinstance(m, dict) else None
    if not isinstance(name, str) or not name:
        return ""
    name = re.sub(r"\s*\((?:1M|\d+[kK]) context\)$", "", sanitize(name))
    return "\x1b[1m" + p("accent", truncate(name, 20)) + "\x1b[22m"


def seg_dir(data, o, p, width):
    cwd = get(data, "workspace", "current_dir") or data.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return ""
    name = sanitize(os.path.basename(cwd.rstrip("/")) or "/")
    icon = "" if o.get("no-icons") else p("info", ICONS["dir"]) + " "
    return icon + p("text", truncate(name, 24 if tier(width) == "narrow" else 40))


def seg_branch(data, o, p, width, info=None):
    if info is None or not info.get("repo"):
        return ""
    ref = info.get("branch")
    if not ref or ref == "(detached)":
        ref = f"({info['oid']})" if info.get("oid") else "(detached)"
    limit = {"wide": 32, "mid": 20, "narrow": 14}[tier(width)]
    icon = "" if o.get("no-icons") else ICONS["branch"] + " "
    return p("accent", icon + truncate(sanitize(ref), limit))


SEP = " \u2502 "


def join(parts, p):
    return p("separator", SEP).join(x for x in parts if x)


def seg_workspace(data, o, p, width):
    """model is native; this renders  dir │ branch +ins −del N modified · ↑a↓b"""
    info = git_cached(data, o)
    git = ""
    if info and info.get("repo"):
        git = seg_branch(data, o, p, width, info)
        detail = git_detail(info, o, p, width)
        if detail:
            git += " " + detail
    return join([seg_dir(data, o, p, width), git], p)


def vis_width(text):
    return sum(cell_width(c) for c in CTRL_RE.sub("", text))


def seg_telemetry(data, o, p, width):
    order = [n.strip() for n in str(o.get("order", "ctx,5h,7d,cache")).split(",")]
    if o.get("fill") and tier(width) != "narrow":
        out = telemetry_fill(data, o, p, width, order)
        if out is not None:
            return out
    sub = {k: v for k, v in o.items() if k not in ("width", "warn", "crit")}
    out = []
    for name in order:
        so = dict(sub)
        for k in ("width", "warn", "crit"):
            if f"{name}-{k}" in o:
                so[k] = o[f"{name}-{k}"]
        fn = SEGMENTS.get(name)
        if fn and name in ("ctx", "5h", "7d", "cache"):
            out.append(fn(data, so, p, width))
    return join(out, p)


BAR_WEIGHTS = {"ctx": 2, "5h": 1, "7d": 1}


def telemetry_fill(data, o, p, width, order):
    """Single row stretched to the full width: bars absorb all free columns."""
    items = []
    for name in order:
        m = metric(name, data, o)
        if m is None:
            continue
        label, pct, color, detail = m
        if name == "ctx":
            detail = detail.replace(" / ", "/")
        elif name in ("5h", "7d"):
            detail = countdown(get(data, "rate_limits", "five_hour" if name == "5h" else "seven_day", "resets_at")) \
                if not o.get("no-reset") else None
        else:
            detail = None
        items.append((name, label, pct, color, detail or ""))
    if not items:
        return ""
    fixed = 3 * (len(items) - 1)
    for name, label, pct, color, detail in items:
        pc = f"{pct:.0f}%" if pct is not None else "—"
        fixed += len(label) + 1 + len(pc) + (1 + vis_width(detail) if detail else 0)
        if name in BAR_WEIGHTS:
            fixed += 1
    weights = {it[0]: BAR_WEIGHTS[it[0]] for it in items if it[0] in BAR_WEIGHTS}
    free = width - fixed
    total = sum(weights.values())
    if total == 0:
        return None
    cap = int(num(o, "bar-max", 10_000))
    bars = {n: min(cap, free * w // total) for n, w in weights.items()}
    if min(bars.values()) < 4:
        return None  # not enough room: caller falls back to compact tiers
    if cap >= 10_000:
        bars["ctx" if "ctx" in bars else next(iter(bars))] += free - sum(bars.values())
    parts = []
    for name, label, pct, color, detail in items:
        pc = f"{pct:.0f}%" if pct is not None else "—"
        seg = p("muted", label) + " "
        if name in bars:
            seg += (bar(pct, bars[name], color, p) if pct is not None else p("separator", EMPTY * bars[name])) + " "
        seg += p(color, pc)
        if detail:
            seg += " " + p("text2", detail)
        parts.append(seg)
    return join(parts, p)


def seg_gitstat(data, o, p, width):
    info = git_cached(data, o)
    if not info or not info.get("repo"):
        return ""
    return git_detail(info, o, p, width)


def fmt_tokens(n):
    if n >= 1_000_000:
        v = n / 1_000_000
        return f"{v:.1f}M".replace(".0M", "M")
    if n >= 1000:
        return f"{n / 1000:.0f}k"
    return str(int(n))


def clock(resets_at):
    """Local wall-clock time of a reset timestamp: '02:29' today, 'Sun 14:00' later."""
    try:
        if isinstance(resets_at, (int, float)):
            ts = float(resets_at) / (1000.0 if resets_at > 1e12 else 1.0)
        else:
            from datetime import datetime
            ts = datetime.fromisoformat(str(resets_at).replace("Z", "+00:00")).timestamp()
        lt, now = time.localtime(ts), time.localtime()
        if lt.tm_yday == now.tm_yday and lt.tm_year == now.tm_year:
            return time.strftime("%H:%M", lt)
        return time.strftime("%a %H:%M", lt)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def metric(name, data, o):
    """-> (label, pct|None, color, detail) or None when the metric must be hidden."""
    if name == "ctx":
        pct = ctx_pct(data)
        detail = ""
        cw = data.get("context_window") if isinstance(data.get("context_window"), dict) else {}
        size = cw.get("context_window_size")
        if isinstance(size, (int, float)) and size > 0:
            u = cw.get("current_usage")
            used = None
            if isinstance(u, dict):
                vals = [u.get(k) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")]
                if any(isinstance(v, (int, float)) for v in vals):
                    used = sum(v for v in vals if isinstance(v, (int, float)))
            if used is None and pct is not None:
                used = pct / 100 * size
            detail = f"{fmt_tokens(used)} / {fmt_tokens(size)}" if used is not None else f"— / {fmt_tokens(size)}"
        color = level(pct, num(o, "ctx-warn", 60), num(o, "ctx-crit", 85), "success") if pct is not None else "muted"
        return "CTX", pct, color, detail
    if name in ("5h", "7d"):
        bucket = "five_hour" if name == "5h" else "seven_day"
        rl = get(data, "rate_limits", bucket)
        pct = as_pct(rl.get("used_percentage")) if isinstance(rl, dict) else None
        if pct is None:
            return None
        color = level(pct, num(o, f"{name}-warn", 70), num(o, f"{name}-crit", 90), "info")
        detail = ""
        if not o.get("no-reset"):
            cd, ck = countdown(rl.get("resets_at")), clock(rl.get("resets_at"))
            if cd:
                detail = f"resets in {cd}" + (f" · {ck}" if ck else "")
        return name.upper(), pct, color, detail
    if name == "cache":
        out = cache_value(data, o)
        if out is None:
            return None
        pct, scope = out
        color = "success" if pct >= 80 else ("warning" if pct >= 50 else "muted")
        return "CACHE", pct, color, "session" if scope == "session" else "last request"
    return None


def seg_meter(data, o, p, width, name=None):
    """One full-width row per metric:  LABEL  ██████░░░░  42%  detail"""
    name = name or (o["_"][1] if len(o["_"]) > 1 else "ctx")
    m = metric(name, data, o)
    if m is None:
        return ""
    label, pct, color, detail = m
    if tier(width) == "narrow":
        pc = f"{pct:.0f}%" if pct is not None else "—"
        return f"{p('muted', label)} {p(color, pc)}"
    dw = int(num(o, "detail-width", 30))
    bw = int(max(10, min(num(o, "bar-max", 64), width - 6 - 2 - 5 - 2 - dw)))
    b = bar(pct, bw, color, p) if pct is not None else p("separator", EMPTY * bw)
    pc = f"{pct:.0f}%".rjust(4) if pct is not None else "   —"
    return (p("muted", label.ljust(6)) + b + "  " + p(color, pc) + "  " + p("text2", truncate(detail, dw)))


def seg_meters(data, o, p, width):
    order = [x.strip() for x in str(o.get("order", "ctx,5h,7d,cache")).split(",")]
    if tier(width) == "narrow":
        return seg_telemetry(data, o, p, width)
    return "\n".join(r for r in (seg_meter(data, o, p, width, n) for n in order) if r)


def head_content(data, o, p, width):
    """Model │ dir │ branch — the boxed identity block of row 1."""
    return join([seg_model(data, o, p, width),
                 seg_workspace(data, {**o, "no-git-detail": True}, p, width)], p)


def prompt_box_content(data, o, p, width, head_w):
    """Last prompt for the second box of row 1: fits between the identity box and gitstat."""
    if o.get("no-prompt-box"):
        return ""
    git_w = vis_width(seg_gitstat(data, o, p, width))
    room = width - head_w - 1 - 4 - (git_w + 3 if git_w else 0)
    if room < 10:
        return ""
    color = o.get("prompt-box-color", "cyan")
    return seg_prompt(data, {**o, "text-color": color, "icon-color": color}, p, room)


def head_boxes(data, o, p, width):
    head = head_content(data, o, p, width)
    if not head:
        return "", ""
    return head, prompt_box_content(data, o, p, width, vis_width(head) + 4)


def box_edge(data, o, p, width, top):
    head, msg = head_boxes(data, o, p, width)
    if not head:
        return ""
    left, right = ("\u256d", "\u256e") if top else ("\u2570", "\u256f")
    out = p(o.get("box-color", "protocol"), left + "\u2500" * (vis_width(head) + 2) + right)
    if msg:
        out += " " + p(o.get("prompt-box-color", "cyan"), left + "\u2500" * (vis_width(msg) + 2) + right)
    return out


def seg_boxtop(data, o, p, width):
    return box_edge(data, o, p, width, True)


def seg_boxbot(data, o, p, width):
    return box_edge(data, o, p, width, False)


def seg_boxmid(data, o, p, width):
    head, msg = head_boxes(data, o, p, width)
    if not head:
        return ""
    bar_ = p(o.get("box-color", "protocol"), "\u2502")
    out = f"{bar_} {head} {bar_}"
    if msg:
        mbar = p(o.get("prompt-box-color", "cyan"), "\u2502")
        out += f" {mbar} {msg} {mbar}"
    return out


def seg_render(data, o, p, width):
    """Standalone renderer: every row in one process, no ccstatusline.
    Row spec: comma-separated segments, '>' starts the right-aligned group, '|' separates rows."""
    spec = str(o.get("lines", "boxtop|boxmid>gitstat|boxbot|telemetry"))
    ro = dict(o)
    ro.setdefault("fill", True)
    rows = []
    for row in spec.split("|"):
        groups = []
        for g, group in enumerate(row.split(">", 1)):
            parts = []
            for name in group.split(","):
                fn = SEGMENTS.get(name.strip())
                if fn and fn is not seg_render:
                    oo = ro
                    if name.strip() == "workspace" and "gitstat" in row:
                        oo = {**ro, "no-git-detail": True}  # detail is rendered on the right instead
                    try:
                        parts.append(fn(data, oo, p, width))
                    except Exception:
                        pass
            groups.append(join(parts, p))
        line = groups[0]
        if len(groups) > 1 and groups[1]:
            gap = width - vis_width(line) - vis_width(groups[1])
            line = line + " " * max(1, gap) + groups[1] if gap >= 1 else line
        if line:
            rows.append(line + "\x1b[0m")
    return "\n".join(rows)


SEGMENTS = {"boxtop": seg_boxtop, "boxmid": seg_boxmid, "boxbot": seg_boxbot, "gap": lambda d, o, p, w: "\u2800", "model": seg_model, "gitstat": seg_gitstat, "meter": seg_meter, "meters": seg_meters, "render": seg_render, "dir": seg_dir, "workspace": seg_workspace, "telemetry": seg_telemetry,"git": seg_git, "ctx": seg_ctx, "cache": seg_cache, "prompt": seg_prompt, "rule": seg_rule,
            "5h": lambda d, o, p, w: seg_limit(d, o, p, w, "five_hour", "5H", 5, True),
            "7d": lambda d, o, p, w: seg_limit(d, o, p, w, "seven_day", "7D", 0, False)}


def probe_width():
    try:
        w = int(os.environ.get("CCSTATUSLINE_WIDTH") or os.environ.get("COLUMNS") or 0)
        if w > 0:
            return w
    except ValueError:
        pass
    try:
        fd = os.open("/dev/tty", os.O_RDONLY | os.O_NOCTTY)
        try:
            return os.get_terminal_size(fd).columns
        finally:
            os.close(fd)
    except OSError:
        return 120


def main():
    o = opts(sys.argv[1:])
    name = o["_"][0] if o["_"] else ""
    fn = SEGMENTS.get(name)
    if fn is None:
        sys.stderr.write(__doc__)
        return 2
    try:
        data = json.loads(sys.stdin.buffer.read(4 * 1024 * 1024) or b"{}")
    except (ValueError, UnicodeDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    width = data.get("terminal_width")
    if not isinstance(width, int) or width <= 0:
        width = probe_width()
    if "cols" in o:
        width = int(num(o, "cols", width))
    width = max(20, width - 5 - 2)  # ccstatusline RESERVED_COLUMNS + widget padding
    try:
        out = fn(data, o, Paint(o.get("theme", "g100")), width)
    except Exception:  # never break the status line
        out = ""
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
