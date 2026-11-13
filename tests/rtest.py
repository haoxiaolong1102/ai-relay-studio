# -*- coding: utf-8 -*-
"""自测公共部分：临时工程 + 临时状态 + 子进程调用。不调用任何 AI 模型，不弹真窗口。"""
import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LIB = os.path.join(ROOT, "lib")
RELAY = os.path.join(ROOT, "bin", "relay")
HOOK_GUARD = os.path.join(ROOT, "bin", "hook_guard.py")
HOOK_BATON = os.path.join(ROOT, "bin", "hook_baton.py")
HOOK_PROBE = os.path.join(ROOT, "bin", "hook_probe.py")
SHIM = os.path.join(ROOT, "shim", "relay-shim.sh")
FAKE = os.path.join(HERE, "fake_codex.py")
PY = "/usr/bin/python3"

# ---- 进程级兜底：就算哪个测试忘了隔离，也指向临时目录，且弹窗只会报“答案用完了” ----
_PROC = os.path.realpath(tempfile.mkdtemp(prefix="relaytest-proc-"))
_empty_gitcfg = os.path.join(_PROC, "gitconfig")
open(_empty_gitcfg, "w").close()
_ans0 = os.path.join(_PROC, "answers.json")
with open(_ans0, "w") as _f:
    _f.write("[]")
_cfg0 = os.path.join(_PROC, "projects.json")
with open(_cfg0, "w") as _f:
    json.dump({"live_enabled": False, "projects": {}}, _f)
os.environ.update({
    "RELAY_STATE": os.path.join(_PROC, "state"), "RELAY_CONFIG": _cfg0,
    "RELAY_HOLDERS": os.path.join(_PROC, "holders.json"), "RELAY_SNAPSHOTS": os.path.join(_PROC, "snaps"),
    "RELAY_UI_ANSWERS": _ans0, "RELAY_CODEX_BIN": FAKE,
    "GIT_CONFIG_GLOBAL": _empty_gitcfg, "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@localhost",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@localhost",
})
for _k in ("RELAY_ROOT", "RELAY_TEST_PAUSE_BETWEEN", "RELAY_TEST_GUARD_HANG", "CLAUDE_PROJECT_DIR"):
    os.environ.pop(_k, None)
atexit.register(lambda: shutil.rmtree(_PROC, ignore_errors=True))
sys.path.insert(0, LIB)
from relaylib import common  # noqa: E402  (必须在设好环境变量之后)


def real(p):
    return os.path.realpath(str(p))


def sh(cmd, cwd=None, env=None, check=True):
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
    if check and p.returncode != 0:
        raise RuntimeError("命令失败 %s\n%s%s" % (cmd, p.stdout, p.stderr))
    return p


class Env(object):
    """一套临时工程 + 临时状态。用完调 cleanup()（addCleanup 即可）。"""

    def __init__(self, git=True, code="X", test_cmd=("/usr/bin/true",), readonly_mcp=(),
                 codex_over=None, extra_files=None):
        self.code = code
        self.git = git
        self.tmp = real(tempfile.mkdtemp(prefix="relaytest-"))
        self.proj = os.path.join(self.tmp, "proj")
        self.state = os.path.join(self.tmp, "state")
        self.holders = os.path.join(self.tmp, "holders", "holders.json")
        self.snaps = os.path.join(self.tmp, "snaps")
        self.answers = os.path.join(self.tmp, "answers.json")
        self.cfgpath = os.path.join(self.tmp, "projects.json")
        self.other = os.path.join(self.tmp, "other")
        self.outside = os.path.join(self.tmp, "outside")
        self._undo = []
        os.makedirs(self.other)
        os.makedirs(self.outside)
        self.write_answers([])
        self._build_project(extra_files or {})
        self.cfg = {
            "live_enabled": True, "holders_copy": self.holders, "snapshots_dir": self.snaps,
            "codex": dict({"bin": FAKE, "model": "fake", "effort": "low", "max_segments": 3,
                           "segment_timeout_min": 1, "total_timeout_min": 5}, **(codex_over or {})),
            "media": {"agy_bin": "/nonexistent/agy", "gemini_eyes_dir": "/nonexistent/ge"},
            "projects": {code: {"name": "测试工程", "path": self.proj, "raw_dirs": ["raw"],
                                "readonly_mcp": list(readonly_mcp)}},
        }
        if test_cmd:
            self.cfg["projects"][code]["test_cmd"] = list(test_cmd)
        self.save_cfg()

    # ---- 建工程 ----
    def _w(self, rel, text, mode=None):
        p = os.path.join(self.proj, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(text if isinstance(text, bytes) else text.encode("utf-8"))
        if mode:
            os.chmod(p, mode)

    def _build_project(self, extra):
        os.makedirs(self.proj)
        self._w("a.txt", "alpha\nx\n")
        self._w("src/main.py", "print('hi')\n")
        self._w("docs/STATE.md", "# 现状\n\n## 下一步\n1. 做点事\n")
        self._w(".gitignore", "docs/relay/\n")
        self._w("raw/notes.txt", "素材笔记\n", 0o640)
        self._w("raw/sub/clip.wav", b"RIFF0000WAVEfmt " + b"\0" * 32)
        self._w("audio/ok.wav", b"RIFF0000WAVEfmt " + b"\0" * 32)
        self._w(".claude/settings.json", "{}\n")
        for k, v in extra.items():
            self._w(k, v)
        if self.git:
            sh(["git", "init", "-q"], cwd=self.proj)
            sh(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=self.proj)
            sh(["git", "add", "-A"], cwd=self.proj)
            sh(["git", "commit", "-q", "-m", "init"], cwd=self.proj)

    def save_cfg(self):
        with open(self.cfgpath, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f, ensure_ascii=False, indent=2)

    def set_cfg(self, **kv):
        self.cfg.update(kv)
        self.save_cfg()

    # ---- 环境 ----
    def env(self, **extra):
        e = dict(os.environ)
        e.update({"RELAY_STATE": self.state, "RELAY_CONFIG": self.cfgpath, "RELAY_HOLDERS": self.holders,
                  "RELAY_SNAPSHOTS": self.snaps, "RELAY_UI_ANSWERS": self.answers, "RELAY_CODEX_BIN": FAKE})
        for k in ("RELAY_ROOT", "RELAY_TEST_PAUSE_BETWEEN", "RELAY_TEST_GUARD_HANG", "CLAUDE_PROJECT_DIR"):
            e.pop(k, None)
        for k, v in extra.items():
            if v is None:
                e.pop(k, None)
            else:
                e[k] = str(v)
        return e

    def inproc(self):
        """让本进程里 import 的 relaylib 指向这套临时状态。"""
        old = (common.STATE, common.CONFIG, os.environ.get("RELAY_HOLDERS"), os.environ.get("RELAY_SNAPSHOTS"))
        common.STATE, common.CONFIG = Path(self.state), Path(self.cfgpath)
        os.environ["RELAY_HOLDERS"], os.environ["RELAY_SNAPSHOTS"] = self.holders, self.snaps

        def undo():
            common.STATE, common.CONFIG = old[0], old[1]
            for k, v in (("RELAY_HOLDERS", old[2]), ("RELAY_SNAPSHOTS", old[3])):
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self._undo.append(undo)

    def write_answers(self, answers):
        with open(self.answers, "w", encoding="utf-8") as f:
            json.dump(list(answers), f, ensure_ascii=False)

    def remaining_answers(self):
        with open(self.answers, encoding="utf-8") as f:
            return json.load(f)

    # ---- 调用 ----
    def relay(self, *args, **kw):
        answers = kw.pop("answers", None)
        timeout = kw.pop("timeout", 180)
        if answers is not None:
            self.write_answers(answers)
        return subprocess.run([PY, RELAY] + list(args), env=self.env(**kw), capture_output=True,
                              text=True, timeout=timeout, cwd=self.tmp)

    def shim(self, mode, data, root=None, holders=None, **kw):
        if not isinstance(data, str):
            data = json.dumps(data, ensure_ascii=False)
        e = self.env(RELAY_ROOT=root or ROOT, **kw)
        if holders:
            e["RELAY_HOLDERS"] = holders
        t0 = time.time()
        p = subprocess.run(["/bin/sh", SHIM, mode], input=data, env=e, capture_output=True, text=True, timeout=60)
        p.elapsed = time.time() - t0
        return p

    # ---- 状态 ----
    def lock_path(self):
        return os.path.join(self.state, "locks", "%s.json" % self.code)

    def set_lock(self, holder, **extra):
        os.makedirs(os.path.dirname(self.lock_path()), exist_ok=True)
        d = {"holder": holder, "since": "2026-01-01 00:00:00", "segment": 0, "by": "test", "seq": 1}
        d.update(extra)
        with open(self.lock_path(), "w", encoding="utf-8") as f:
            json.dump(d, f)

    def lock_holder(self):
        try:
            with open(self.lock_path(), encoding="utf-8") as f:
                return json.load(f)["holder"]
        except FileNotFoundError:
            return "claude"

    def set_holders(self, holder, live=True):
        os.makedirs(os.path.dirname(self.holders), exist_ok=True)
        with open(self.holders, "w", encoding="utf-8") as f:
            json.dump({"seq": 1, "updated": "x", "live_enabled": live,
                       "projects": {self.code: {"holder": holder, "path": self.proj}}}, f)

    def holders_holder(self):
        with open(self.holders, encoding="utf-8") as f:
            return json.load(f)["projects"][self.code]["holder"]

    def audit_text(self, code="__code__"):
        code = self.code if code == "__code__" else code
        p = os.path.join(self.state, "audit", "%s.log" % (code or "_global"))
        try:
            with open(p, encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return ""

    def read(self, rel):
        with open(os.path.join(self.proj, rel), encoding="utf-8") as f:
            return f.read()

    def exists(self, rel):
        return os.path.lexists(os.path.join(self.proj, rel))

    def tree(self, root=None, skip=("raw", "docs/relay", ".git")):
        """{相对路径: 内容}，跳过素材区、接力文件夹、.git。"""
        root = root or self.proj
        out = {}
        for dp, dns, fns in os.walk(root):
            rel_dir = os.path.relpath(dp, root)
            rel_dir = "" if rel_dir == "." else rel_dir
            dns[:] = [d for d in dns if os.path.join(rel_dir, d) not in skip]
            for n in fns:
                rel = os.path.join(rel_dir, n)
                with open(os.path.join(dp, n), "rb") as f:
                    out[rel] = f.read()
        return out

    def summary(self):
        p = os.path.join(self.state, "runs", self.code, "latest-summary.json")
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    # ---- 清理 ----
    def cleanup(self):
        for u in self._undo:
            try:
                u()
            except Exception:
                pass
        try:
            self.relay("unprotect", self.code, timeout=60)
        except Exception:
            pass
        for cmd in (["chflags", "-R", "nouchg", self.tmp], ["chmod", "-R", "u+w", self.tmp]):
            subprocess.run(cmd, capture_output=True)
        shutil.rmtree(self.tmp, ignore_errors=True)


def hook(tool, cwd, session="s1", event="PostToolUse", **ti):
    d = {"session_id": session, "cwd": cwd, "hook_event_name": event, "tool_name": tool, "tool_input": ti}
    return d
