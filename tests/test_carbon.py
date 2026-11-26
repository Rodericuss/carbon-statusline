#!/usr/bin/env python3
"""Acceptance tests for carbon.py (spec §12). Run: python3 -m unittest -v tests/test_carbon.py"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "carbon.py")
ANSI = re.compile(r"\x1b\[[0-9;]*m")
GREEN, YELLOW, RED, BLUE = "38;2;66;190;101", "38;2;241;194;27", "38;2;250;77;86", "38;2;120;169;255"


def run(seg, payload, *args, cols=130, raw=False):
    data = payload if isinstance(payload, (bytes, str)) else json.dumps(payload)
    env = {**os.environ, "COLUMNS": str(cols), "XDG_RUNTIME_DIR": RUNTIME}
    r = subprocess.run([SCRIPT, seg, *args], input=data if isinstance(data, bytes) else data.encode(),
                       capture_output=True, env=env, timeout=5)
    assert r.returncode == 0, r.stderr
    out = r.stdout.decode()
    return out if raw else ANSI.sub("", out)


def git(cwd, *a):
    subprocess.run(["git", "-C", cwd, "-c", "user.email=t@t", "-c", "user.name=t", *a],
                   check=True, capture_output=True)


def transcript(path, entries):
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    return path


def user(content, **kw):
    return {"type": "user", "message": {"role": "user", "content": content}, **kw}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global RUNTIME
        cls.tmp = tempfile.mkdtemp(prefix="carbon-test-")
        RUNTIME = os.path.join(cls.tmp, "run")
        os.makedirs(RUNTIME)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)


class Context(Base):
    def test_new_session_unavailable(self):
        out = run("ctx", {"context_window": {"context_window_size": 200000}})
        self.assertIn("CTX", out)
        self.assertIn("—", out)
        self.assertNotIn("0%", out)

    def test_no_context_object(self):
        self.assertIn("—", run("ctx", {}))

    def test_levels_and_colors(self):
        for pct, color in ((0, GREEN), (50, GREEN), (60, YELLOW), (84, YELLOW), (85, RED), (100, RED)):
            raw = run("ctx", {"context_window": {"used_percentage": pct}}, raw=True)
            self.assertIn(f"{pct}%", ANSI.sub("", raw))
            self.assertIn(color + "m" + f"{pct}%", raw, pct)

    def test_bar_width_and_fraction(self):
        out = run("ctx", {"context_window": {"used_percentage": 42}})
        bar = out.split()[1]
        self.assertEqual(len(bar), 10)
        self.assertEqual(bar, "████▎░░░░░")
        self.assertEqual(run("ctx", {"context_window": {"used_percentage": 100}}).split()[1], "█" * 10)
        self.assertEqual(run("ctx", {"context_window": {"used_percentage": 0}}).split()[1], "░" * 10)

    def test_fallback_uses_window_size_not_200k(self):
        p = {"context_window": {"context_window_size": 1_000_000, "current_usage": {
            "input_tokens": 10_000, "cache_read_input_tokens": 90_000, "cache_creation_input_tokens": 0}}}
        self.assertIn("10%", run("ctx", p))

    def test_compaction_drop(self):
        self.assertIn("91%", run("ctx", {"context_window": {"used_percentage": 91}}))
        self.assertIn("12%", run("ctx", {"context_window": {"used_percentage": 12}}))

    def test_ignores_cumulative_totals(self):
        p = {"context_window": {"total_input_tokens": 5_000_000, "context_window_size": 200000}}
        self.assertIn("—", run("ctx", p))


class Limits(Base):
    def test_five_hour(self):
        p = {"rate_limits": {"five_hour": {"used_percentage": 36, "resets_at": time.time() + 2 * 3600 + 14 * 60 + 30}}}
        raw = run("5h", p, raw=True)
        out = ANSI.sub("", raw)
        self.assertRegex(out, r"^5H [█▏▎▍▌▋▊▉░]{5} 36% 2h14m$")
        self.assertIn(BLUE, raw)

    def test_countdown_formats(self):
        for secs, want in ((14 * 60 + 5, "14m"), (20, "<1m"), (-100, "<1m")):
            p = {"rate_limits": {"five_hour": {"used_percentage": 1, "resets_at": time.time() + secs}}}
            self.assertTrue(run("5h", p).endswith(want), (secs, run("5h", p)))

    def test_iso_reset(self):
        from datetime import datetime, timedelta, timezone
        ts = (datetime.now(timezone.utc) + timedelta(minutes=30, seconds=20)).isoformat()
        self.assertTrue(run("5h", {"rate_limits": {"five_hour": {"used_percentage": 1, "resets_at": ts}}}).endswith("30m"))

    def test_thresholds(self):
        for pct, color in ((69, BLUE), (70, YELLOW), (90, RED)):
            self.assertIn(color, run("7d", {"rate_limits": {"seven_day": {"used_percentage": pct}}}, raw=True))

    def test_missing_is_hidden_not_zero(self):
        for p in ({}, {"rate_limits": {}}, {"rate_limits": {"five_hour": None}}, {"rate_limits": {"five_hour": {}}}):
            self.assertEqual(run("5h", p), "")
            self.assertEqual(run("7d", p), "")

    def test_weekly_reset_optional(self):
        p = {"rate_limits": {"seven_day": {"used_percentage": 78, "resets_at": time.time() + 3 * 86400 + 3700}}}
        self.assertEqual(run("7d", p), "7D 78%")
        self.assertEqual(run("7d", p, "--reset"), "7D 78% 3d1h")


class Cache(Base):
    def test_native_ratio(self):
        raw = run("cache", {"prompt_cache": {"hit_ratio": 0.94}}, raw=True)
        self.assertEqual(ANSI.sub("", raw), "CACHE 94%")
        self.assertIn(GREEN, raw)

    def test_request_fallback_is_labelled(self):
        p = {"context_window": {"current_usage": {"input_tokens": 100, "cache_read_input_tokens": 700,
                                                  "cache_creation_input_tokens": 200}}}
        self.assertEqual(run("cache", p), "CACHE·REQ 70%")
        self.assertIn(YELLOW, run("cache", p, raw=True))

    def test_low_is_muted_not_error(self):
        raw = run("cache", {"prompt_cache": {"hit_ratio": 0.1}}, raw=True)
        self.assertNotIn(RED, raw)

    def test_missing_or_zero_denominator_hidden(self):
        self.assertEqual(run("cache", {}), "")
        z = {"context_window": {"current_usage": {"input_tokens": 0, "cache_read_input_tokens": 0,
                                                  "cache_creation_input_tokens": 0}}}
        self.assertEqual(run("cache", z), "")

    def test_scope_session_only(self):
        p = {"context_window": {"current_usage": {"input_tokens": 1, "cache_read_input_tokens": 1,
                                                  "cache_creation_input_tokens": 0}}}
        self.assertEqual(run("cache", p, "--scope", "session"), "")


class Git(Base):
    def make_repo(self, name):
        d = os.path.join(self.tmp, name)
        os.makedirs(d)
        git(d, "init", "-q", "-b", "main")
        with open(os.path.join(d, "f"), "w") as f:
            f.write("a\nb\n")
        git(d, "add", "f")
        git(d, "commit", "-qm", "init")
        return d

    def ws(self, d, *a, cols=130):
        return run("workspace", {"workspace": {"current_dir": d}}, "--git-ttl", "0", *a, cols=cols)

    def test_outside_repo(self):
        d = os.path.join(self.tmp, "plain")
        os.makedirs(d)
        self.assertEqual(self.ws(d), "󰉋 plain")

    def test_clean(self):
        d = self.make_repo("clean")
        self.assertEqual(self.ws(d), "󰉋 clean │ \ue0a0 main clean")

    def test_dirty(self):
        d = self.make_repo("dirty")
        with open(os.path.join(d, "f"), "w") as f:
            f.write("a\nc\nd\n")
        open(os.path.join(d, "new"), "w").close()
        self.assertEqual(self.ws(d), "󰉋 dirty │ \ue0a0 main +2 −1 2 modified")

    def test_detached(self):
        d = self.make_repo("detached")
        git(d, "checkout", "-q", "--detach")
        self.assertRegex(self.ws(d), r" \([0-9a-f]{7}\) clean$")

    def test_worktree(self):
        d = self.make_repo("wt-main")
        w = os.path.join(self.tmp, "wt-feature")
        git(d, "worktree", "add", "-q", "-b", "feature/x", w)
        self.assertEqual(self.ws(w), "󰉋 wt-feature │ \ue0a0 feature/x clean")

    def test_upstream_ahead_behind_and_missing(self):
        origin = self.make_repo("origin")
        clone = os.path.join(self.tmp, "clone")
        subprocess.run(["git", "clone", "-q", origin, clone], check=True, capture_output=True)
        self.assertTrue(self.ws(clone).endswith("clean"))
        git(clone, "commit", "-q", "--allow-empty", "-m", "x")
        git(clone, "commit", "-q", "--allow-empty", "-m", "y")
        self.assertTrue(self.ws(clone).endswith("clean · ↑2"))
        git(clone, "checkout", "-q", "-b", "local-only")
        self.assertNotIn("↑", self.ws(clone))

    def test_unborn_branch(self):
        d = os.path.join(self.tmp, "unborn")
        os.makedirs(d)
        git(d, "init", "-q", "-b", "main")
        self.assertEqual(self.ws(d), "󰉋 unborn │ \ue0a0 main clean")

    def test_mid_width_shortens(self):
        d = self.make_repo("mid")
        open(os.path.join(d, "n"), "w").close()
        self.assertTrue(self.ws(d, cols=90).endswith("1M"))

    def test_narrow_hides_detail(self):
        d = self.make_repo("narrow")
        open(os.path.join(d, "n"), "w").close()
        self.assertEqual(self.ws(d, cols=60), "󰉋 narrow │ \ue0a0 main")

    def test_injection_in_branch_and_dir(self):
        d = os.path.join(self.tmp, "evil\x1b[31mdir")
        os.makedirs(d)
        out = run("workspace", {"workspace": {"current_dir": d}}, raw=True)
        self.assertNotIn("\x1b[31m", out)


class Prompt(Base):
    def p(self, name, entries, cols=130, sid=None, *a):
        path = transcript(os.path.join(self.tmp, name + ".jsonl"), entries)
        return run("prompt", {"transcript_path": path, "session_id": sid or name}, *a, cols=cols)

    def test_skips_noise(self):
        out = self.p("noise", [
            user("Refactor the event pipeline"),
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}},
            user([{"type": "tool_result", "tool_use_id": "t", "content": "x"}], toolUseResult={}),
            user("[Request interrupted by user for tool use]"),
            user("<command-name>/clear</command-name>"),
            user("Caveat: The messages below were generated by the user while running local commands."),
            user("<system-reminder>hidden</system-reminder>", isMeta=True),
            user("sidechain", isSidechain=True),
            user("This session is being continued...", isCompactSummary=True),
        ])
        self.assertEqual(out, "󰍩 Refactor the event pipeline")

    def test_multimodal(self):
        out = self.p("mm", [user([{"type": "image", "source": {"type": "base64", "data": "AAA"}},
                                  {"type": "text", "text": "[Image #1]\n\nwhat is\n\n  this?"}])])
        self.assertEqual(out, "󰍩 what is this?")

    def test_pasted_blocks_collapsed(self):
        out = self.p("paste", [user([{"type": "text", "text": "[Image #1]\n\n<pasted_content id=\"x\">\n# Spec\nlots\n</pasted_content id=\"x\">\n\nfix the bar"}])])
        self.assertEqual(out, "󰍩 [paste] fix the bar")

    def test_paste_chip_boxed_in_protocol_color(self):
        path = transcript(os.path.join(self.tmp, "chip.jsonl"), [user("<pasted_content id=\"x\">spec</pasted_content id=\"x\"> fix it")])
        raw = run("prompt", {"transcript_path": path, "session_id": "chip"}, "--paste-box", raw=True)
        self.assertIn("\x1b[53;4;58;2;61;219;217m", raw)  # overline + underline in #3DDBD9
        self.assertIn("\x1b[38;2;61;219;217m\u2595", raw)
        self.assertEqual(ANSI.sub("", raw), "󰍩 \u2595paste\u258f fix it")

    def test_truncation_by_cells(self):
        out = self.p("long", [user("界" * 200)], 60)
        body = out[2:]
        self.assertTrue(body.endswith("…"))
        self.assertLessEqual(sum(2 if c == "界" else 1 for c in body), 60 - 7 - 2)

    def test_max_length_option(self):
        out = self.p("max", [user("x" * 200)], 130, None, "--max", "20")
        self.assertEqual(len(out[2:]), 20)

    def test_ansi_injection(self):
        out = run("prompt", {"transcript_path": transcript(os.path.join(self.tmp, "inj.jsonl"), [
            user("hello \x1b]0;pwned\x07\x1b[2J\x1b[31mred\x07‮bidi")]), "session_id": "inj"}, raw=True)
        visible = ANSI.sub("", out)
        self.assertNotIn("\x1b]", out)
        self.assertNotIn("\x1b[2J", out)
        self.assertNotIn("\x07", out)
        self.assertNotIn("‮", visible)
        self.assertIn("hello", visible)

    def test_long_transcript_bounded_and_fast(self):
        path = os.path.join(self.tmp, "big.jsonl")
        filler = json.dumps({"type": "assistant", "message": {"role": "assistant",
                             "content": [{"type": "text", "text": "y" * 2000}]}}) + "\n"
        with open(path, "w") as f:
            f.write(json.dumps(user("first")) + "\n")
            for _ in range(25_000):  # ~50 MB
                f.write(filler)
            f.write(json.dumps(user("latest instruction")) + "\n")
            for _ in range(200):
                f.write(filler)
        t = time.time()
        out = run("prompt", {"transcript_path": path, "session_id": "big"})
        self.assertEqual(out, "󰍩 latest instruction")
        self.assertLess(time.time() - t, 0.5)

    def test_concurrent_sessions_isolated(self):
        a = self.p("sess-a", [user("alpha task")], sid="A")
        b = self.p("sess-b", [user("beta task")], sid="B")
        self.assertEqual((a, b), ("󰍩 alpha task", "󰍩 beta task"))
        self.assertEqual(self.p("sess-a", [user("alpha task")], sid="A"), a)

    def test_cache_invalidates_on_append(self):
        path = transcript(os.path.join(self.tmp, "grow.jsonl"), [user("one")])
        self.assertEqual(run("prompt", {"transcript_path": path, "session_id": "g"}), "󰍩 one")
        with open(path, "a") as f:
            f.write(json.dumps(user("two")) + "\n")
        self.assertEqual(run("prompt", {"transcript_path": path, "session_id": "g"}), "󰍩 two")

    def test_missing_transcript(self):
        self.assertEqual(run("prompt", {"transcript_path": "/nonexistent"}), "")


class Robustness(Base):
    def test_malformed_inputs(self):
        for bad in (b"", b"{", b"null", b"[]", b"\xff\xfe", b'{"context_window": "x", "rate_limits": [1]}',
                    b'{"context_window": {"used_percentage": "42"}}', b'{"model": 5}'):
            for seg in ("render", "workspace", "telemetry", "ctx", "5h", "7d", "cache", "prompt", "rule"):
                run(seg, bad)

    def test_unknown_segment_is_non_fatal_for_ccstatusline(self):
        r = subprocess.run([SCRIPT, "nope"], input=b"{}", capture_output=True)
        self.assertEqual(r.returncode, 2)

    def test_render_layout(self):
        p = {"model": {"display_name": "Opus"}, "workspace": {"current_dir": "/"},
             "context_window": {"used_percentage": 42, "context_window_size": 200000},
             "rate_limits": {"five_hour": {"used_percentage": 36, "resets_at": time.time() + 8040},
                             "seven_day": {"used_percentage": 78}},
             "prompt_cache": {"hit_ratio": 0.94}}
        raw = run("render", p, cols=150, raw=True)
        lines = ANSI.sub("", raw).split("\n")
        self.assertEqual(lines[:3], ["┌────────────┐", "│ Opus │ 󰉋 / │", "└────────────┘"])
        self.assertIn("38;2;61;219;217m┌", raw)  # border in the protocol color
        self.assertRegex(lines[3], r"^CTX \S+ 42% 84k/200k │ 5H \S+ 36% 2h1\dm │ 7D \S+ 78% │ CACHE 94%$")
        self.assertEqual(len(lines[3]), 150 - 7)
        self.assertEqual(len(lines), 4)  # no transcript -> no prompt box, no extra rows

    def test_prompt_box_beside_identity_box(self):
        tmp = tempfile.mkdtemp(dir=self.tmp)
        path = transcript(os.path.join(tmp, "t.jsonl"), [user("fix the bar " + "x" * 300)])
        p = {"model": {"display_name": "Opus"}, "workspace": {"current_dir": "/"},
             "transcript_path": path, "session_id": "pbox"}
        raw = run("render", p, cols=100, raw=True)
        lines = ANSI.sub("", raw).split("\n")
        self.assertTrue(lines[1].startswith("│ Opus │ 󰉋 / │ │ 󰍩 fix the bar x"))
        self.assertTrue(lines[1].endswith("… │"))
        self.assertEqual(len(lines[0]), len(lines[1]))
        self.assertEqual(len(lines[0]), len(lines[2]))
        self.assertLessEqual(len(lines[1]), 100 - 7)
        self.assertIn("38;2;61;219;217m┌", raw.split("┐", 1)[1])  # second box in cyan
        self.assertIn("38;2;198;198;198mfix the bar", raw)  # message text keeps text2
        for seg in ("boxtop", "boxmid", "boxbot"):
            self.assertEqual(run(seg, p, cols=100), lines[("boxtop", "boxmid", "boxbot").index(seg)])

    def test_box_edges_match_content(self):
        d = tempfile.mkdtemp(dir=self.tmp)
        git(d, "init", "-q", "-b", "feature/long-name")
        lines = run("render", {"model": {"display_name": "Opus 5.5"}, "workspace": {"current_dir": d}},
                    "--git-ttl", "0", cols=150).split("\n")
        self.assertEqual(len(lines[0]), len(lines[2]))
        self.assertEqual(lines[1][len(lines[0]) - 1], "│")  # right border closes under ┐

    def test_fill_right_aligns_git(self):
        d = tempfile.mkdtemp(dir=self.tmp)
        git(d, "init", "-q", "-b", "main")
        open(os.path.join(d, "n"), "w").close()
        line = run("render", {"model": {"display_name": "Opus"}, "workspace": {"current_dir": d}},
                   "--git-ttl", "0", cols=150).split("\n")[1]
        self.assertTrue(line.endswith(" 1 modified"))
        self.assertEqual(len(line), 150 - 7)

    def test_fill_falls_back_when_tight(self):
        p = {"context_window": {"used_percentage": 42},
             "rate_limits": {"five_hour": {"used_percentage": 36}, "seven_day": {"used_percentage": 78}}}
        self.assertEqual(len(run("telemetry", p, "--fill", cols=80)), 80 - 7)  # fits: fills
        p["context_window"]["context_window_size"] = 200000
        p["prompt_cache"] = {"hit_ratio": 0.9}
        for k in ("five_hour", "seven_day"):
            p["rate_limits"][k]["resets_at"] = time.time() + 9999
        self.assertEqual(run("telemetry", p, "--fill", cols=80),
                         "CTX ██▏░░ 42% │ 5H 36% 2h46m │ 7D 78% │ CACHE 90%")  # too tight: compact

    def test_meter_bar_scales_with_width(self):
        p = {"context_window": {"used_percentage": 50, "context_window_size": 1_000_000}}
        wide, mid = run("meter", p, "ctx", cols=150), run("meter", p, "ctx", cols=90)
        self.assertGreater(len(wide), len(mid))
        self.assertTrue(wide.endswith("500k / 1M"))

    def test_meter_unavailable(self):
        self.assertRegex(run("meter", {"context_window": {"context_window_size": 200000}}, "ctx"),
                         r"^CTX   ░+     —  — / 200k$")
        self.assertEqual(run("meter", {}, "5h"), "")
        self.assertEqual(run("meter", {}, "cache"), "")

    def test_meters_narrow_fallback(self):
        p = {"context_window": {"used_percentage": 42}}
        self.assertEqual(run("meters", p, cols=60), "CTX 42%")

    def test_narrow_priority(self):
        p = {"model": {"display_name": "Opus"}, "context_window": {"used_percentage": 42},
             "rate_limits": {"five_hour": {"used_percentage": 36, "resets_at": time.time() + 999}},
             "prompt_cache": {"hit_ratio": 0.9}}
        line = run("telemetry", p, cols=60)
        self.assertEqual(line, "CTX 42% │ 5H 36%")

    def test_render_speed(self):
        p = {"model": {"display_name": "Opus"}, "workspace": {"current_dir": HERE},
             "context_window": {"used_percentage": 42}}
        t = time.time()
        run("render", p)
        self.assertLess(time.time() - t, 0.1)


if __name__ == "__main__":
    unittest.main()
