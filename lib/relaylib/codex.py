# -*- coding: utf-8 -*-
"""调用 Codex（只用命令行临时参数，不改它的配置文件），一次一段。

state/runs/ 里只留事件类型、耗时、token 数、命令摘要（脱敏），不留工具输出正文。
"""
import glob
import json
import os
import queue
import signal
import subprocess
import threading
import time

from . import common
from .redact import one_line, redact

STRIP_ENV = ("OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")


def codex_bin(cfg):
    return os.environ.get("RELAY_CODEX_BIN") or cfg["codex"]["bin"]


def is_fake(cfg):
    return bool(os.environ.get("RELAY_CODEX_BIN"))


def network_allowed(cfg):
    """Codex 接力时能不能上网。配置 codex.network_access；没写按不允许。"""
    return bool(cfg["codex"].get("network_access", False))


SANDBOX_MODES = ("read-only", "workspace-write", "danger-full-access")


def sandbox_mode(cfg):
    """Codex 的沙箱档位。配置 codex.sandbox；没写或写错都按最稳的“只能写工程文件夹”。
    danger-full-access＝整台电脑全权、不设沙箱（风险自负，见 README）。"""
    m = cfg["codex"].get("sandbox", "workspace-write")
    return m if m in SANDBOX_MODES else "workspace-write"


def sandbox_args(cfg):
    c = cfg["codex"]
    return ["-s", sandbox_mode(cfg), "-m", c["model"],
            "-c", "model_reasoning_effort=%s" % c["effort"],
            "-c", "sandbox_workspace_write.network_access=%s" % ("true" if network_allowed(cfg) else "false")]


def build_cmd(cfg, project_path, out_file, prompt, is_git):
    cmd = [codex_bin(cfg), "exec", "-C", project_path] + sandbox_args(cfg) + ["--json", "-o", out_file]
    if not is_git:
        cmd.append("--skip-git-repo-check")
    return cmd + [prompt]


def version(cfg):
    try:
        return subprocess.run([codex_bin(cfg), "--version"], capture_output=True, text=True,
                              timeout=20).stdout.strip()
    except Exception:
        return "unknown"


def _summarize(ev):
    """一条事件 → 只留类型、命令摘要、退出码、用量。"""
    out = {"type": one_line(ev.get("type"), 60)}
    item = ev.get("item") if isinstance(ev.get("item"), dict) else None
    if item:
        out["item"] = one_line(item.get("type"), 60)
        if item.get("command") is not None:
            cmd = item["command"]
            out["cmd"] = one_line(" ".join(cmd) if isinstance(cmd, list) else cmd, 200)
        for k in ("exit_code", "status"):
            if item.get(k) is not None:
                out[k] = item[k] if isinstance(item[k], (int, float)) else one_line(item[k], 40)
    for k in ("usage", "rate_limits"):
        if isinstance(ev.get(k), dict):
            out[k] = ev[k]
    if ev.get("thread_id"):
        out["thread_id"] = one_line(ev["thread_id"], 80)
    if ev.get("type") == "error" or isinstance(ev.get("error"), (dict, str)):
        msg = ev.get("message") or (ev["error"].get("message") if isinstance(ev.get("error"), dict) else ev.get("error"))
        out["error"] = one_line(msg, 300)
    return out


def _denied(ev):
    item = ev.get("item") if isinstance(ev.get("item"), dict) else {}
    text = str(item.get("aggregated_output") or item.get("output") or "")
    return ("Operation not permitted" in text or "sandbox" in text.lower()) and item.get("exit_code") not in (0, None)


def rollout_limits(thread_id):
    """从 Codex 自己的会话记录里读额度百分比（事件流里没有时的替代来源）。"""
    if not thread_id:
        return None
    last = None
    for path in glob.glob(os.path.expanduser("~/.codex/sessions/*/*/*/rollout-*%s.jsonl" % thread_id)):
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if '"rate_limits"' not in line:
                        continue
                    try:
                        p = json.loads(line).get("payload") or {}
                    except ValueError:
                        continue
                    rl = p.get("rate_limits") or {}
                    if rl.get("primary") or rl.get("secondary"):
                        last = {"primary": rl.get("primary"), "secondary": rl.get("secondary")}
        except OSError:
            continue
    return last


def run_segment(cfg, code, project_path, seg, prompt, is_git, timeout_s, stop_flag=None, on_start=None):
    """跑一段。返回结果 dict。"""
    runs = common.ensure_private_dir(common.STATE / "runs" / code)
    run_id = "%s-seg%d" % (common.stamp(), seg)
    out_file = str(runs / ("%s.last.txt" % run_id))
    log_file = str(runs / ("%s.jsonl" % run_id))
    env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
    cmd = build_cmd(cfg, project_path, out_file, prompt, is_git)
    t0 = time.time()
    res = {"run_id": run_id, "segment": seg, "rc": None, "timed_out": False, "stopped": False,
           "thread_id": None, "usage": None, "rate_limits": None, "denials": 0, "errors": [],
           "commands": 0, "quota_hit": False}
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, env=env, start_new_session=True)
    if on_start:
        on_start(proc.pid)
    q = queue.Queue()

    def reader():
        for raw in proc.stdout:
            q.put(raw)
        q.put(None)

    threading.Thread(target=reader, daemon=True).start()
    fd = os.open(log_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        while True:
            try:
                raw = q.get(timeout=1.0)
            except queue.Empty:
                raw = b""
            if raw is None:
                break
            if raw:
                try:
                    ev = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    ev = None
                if isinstance(ev, dict):
                    s = _summarize(ev)
                    s["t"] = round(time.time() - t0, 1)
                    os.write(fd, (json.dumps(s, ensure_ascii=False) + "\n").encode("utf-8"))
                    if s.get("thread_id"):
                        res["thread_id"] = s["thread_id"]
                    if s.get("usage"):
                        res["usage"] = s["usage"]
                    if s.get("rate_limits"):
                        res["rate_limits"] = s["rate_limits"]
                    if "cmd" in s and s["type"].endswith("completed"):
                        res["commands"] += 1
                    if _denied(ev):
                        res["denials"] += 1
                    if s.get("error"):
                        res["errors"].append(s["error"])
                        if "usage limit" in s["error"].lower() or "rate limit" in s["error"].lower():
                            res["quota_hit"] = True
            if time.time() - t0 > timeout_s:
                res["timed_out"] = True
            elif stop_flag and os.path.exists(stop_flag) and _stop_now(stop_flag):
                res["stopped"] = True
            if res["timed_out"] or res["stopped"]:
                _kill(proc)
                break
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            _kill(proc, hard=True)
            proc.wait()
    finally:
        os.close(fd)
    res["rc"] = proc.returncode
    res["seconds"] = round(time.time() - t0, 1)
    if not res["rate_limits"]:
        res["rate_limits"] = rollout_limits(res["thread_id"])
    try:
        with open(out_file, encoding="utf-8", errors="replace") as f:
            raw_last = f.read()
        res["last_message"] = one_line(raw_last, 600)
        common.atomic_write_text(out_file, redact(raw_last, 4000) + "\n")   # 原文不留，只留脱敏后的
    except OSError:
        res["last_message"] = ""
    common.atomic_write_json(runs / ("%s.result.json" % run_id), res)
    return res


def _stop_now(stop_flag):
    try:
        with open(stop_flag, encoding="utf-8") as f:
            return f.read().strip() == "now"
    except OSError:
        return False


def _kill(proc, hard=False):
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL if hard else signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass


def load_prompt(cfg, seg, max_seg):
    with open(common.ROOT / "config" / "codex_prompt.md", encoding="utf-8") as f:
        text = f.read()
    return (text.replace("{SEGMENT}", str(seg)).replace("{MAX_SEGMENTS}", str(max_seg))
            .replace("{MEDIA_ASK}", str(common.ROOT / "bin" / "media_ask")))
