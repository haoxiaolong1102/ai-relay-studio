# -*- coding: utf-8 -*-
"""调用 Antigravity 命令行（agy）无人值守跑一段。返回值的字段和 codex.run_segment 一样，交接流程可以通用。

实测（2026-11-13，agy 1.5.7）：
- `agy -p <提示> --output-format json` 在当前目录当工程干活，最后在标准输出打一行 JSON：
  status、response、usage、denied_actions。
- 默认权限下，无人值守时任何终端命令都会被自动拒绝，而且一被拒这一轮就结束、response 为空、status 仍是 SUCCESS，
  所以要看 denied_actions，不能只信 status。
- 配置 antigravity.permissions：
    "full"  → --dangerously-skip-permissions（整台电脑全权；风险自负，见 README）
    "edits" → --mode accept-edits --sandbox（只能改工程里的文件，跑不了命令）
state/runs/ 里只留用量、被拒动作的名字、脱敏后的最后一段回复，不留工具输出正文。
"""
import json
import os
import signal
import subprocess
import time

from . import common
from .redact import one_line, redact

STRIP_ENV = ("OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY")
QUOTA_WORDS = ("quota", "rate limit", "resource_exhausted", "resource exhausted", "usage limit", "out of credits", "额度")


def settings(cfg):
    a = cfg.get("antigravity") or {}
    return {
        "bin": os.environ.get("RELAY_AGY_BIN") or os.path.expanduser(a.get("bin") or "~/.local/bin/agy"),
        "model": a.get("model") or "gemini-4-argon-medium",
        "permissions": a.get("permissions") if a.get("permissions") in ("full", "edits") else "edits",
    }


def is_fake():
    return bool(os.environ.get("RELAY_AGY_BIN"))


def permissions(cfg):
    return settings(cfg)["permissions"]


def build_cmd(cfg, prompt, timeout_s, conversation_id=None):
    st = settings(cfg)
    cmd = [st["bin"], "-p", prompt, "--model", st["model"], "--output-format", "json",
           "--print-timeout", "%ds" % int(timeout_s)]
    if conversation_id:
        cmd += ["--conversation", str(conversation_id)]
    if st["permissions"] == "full":
        cmd.append("--dangerously-skip-permissions")
    else:
        cmd += ["--mode", "accept-edits", "--sandbox"]
    return cmd


def load_prompt(cfg, seg, max_seg):
    with open(common.ROOT / "config" / "agy_prompt.md", encoding="utf-8") as f:
        text = f.read()
    return (text.replace("{SEGMENT}", str(seg)).replace("{MAX_SEGMENTS}", str(max_seg))
            .replace("{MEDIA_ASK}", str(common.ROOT / "bin" / "media_ask")))


def _last_json(text):
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                return obj
    return None


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


def run_segment(cfg, code, project_path, seg, prompt, is_git, timeout_s, stop_flag=None, on_start=None, conversation_id=None):
    runs = common.ensure_private_dir(common.STATE / "runs" / code)
    run_id = "%s-agy-seg%d" % (common.stamp(), seg)
    raw_file = str(runs / ("%s.raw.tmp" % run_id))
    env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
    res = {"run_id": run_id, "segment": seg, "rc": None, "timed_out": False, "stopped": False,
           "thread_id": None, "usage": None, "rate_limits": None, "denials": 0, "errors": [],
           "commands": 0, "quota_hit": False, "tool": "antigravity"}
    t0 = time.time()
    fd = os.open(raw_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        proc = subprocess.Popen(build_cmd(cfg, prompt, timeout_s, conversation_id=conversation_id), cwd=project_path, stdin=subprocess.DEVNULL,
                                stdout=fd, stderr=subprocess.STDOUT, env=env, start_new_session=True)
    finally:
        os.close(fd)
    if on_start:
        on_start(proc.pid)
    while proc.poll() is None:
        time.sleep(1.0)
        if time.time() - t0 > timeout_s + 30:       # agy 自己有 --print-timeout，这里多等 30 秒兜底
            res["timed_out"] = True
        elif stop_flag and os.path.exists(stop_flag) and _stop_now(stop_flag):
            res["stopped"] = True
        if res["timed_out"] or res["stopped"]:
            _kill(proc)
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                _kill(proc, hard=True)
                proc.wait()
            break
    res["rc"] = proc.returncode
    res["seconds"] = round(time.time() - t0, 1)
    try:
        with open(raw_file, encoding="utf-8", errors="replace") as f:
            out = f.read()
    except OSError:
        out = ""
    try:
        os.unlink(raw_file)                          # 原始输出不留（自己刚建的临时文件）
    except OSError:
        pass
    obj = _last_json(out) or {}
    res["thread_id"] = one_line(obj.get("conversation_id"), 80) if obj.get("conversation_id") else None
    if isinstance(obj.get("usage"), dict):
        res["usage"] = obj["usage"]
    denied = obj.get("denied_actions") if isinstance(obj.get("denied_actions"), list) else []
    res["denials"] = len(denied)
    res["denied_actions"] = [one_line((d or {}).get("action"), 60) for d in denied if isinstance(d, dict)][:20]
    status = str(obj.get("status") or "")
    low = out.lower()
    if not obj:
        res["errors"].append(one_line(out[-300:], 300) or "Antigravity 没有输出任何结果")
    elif status and status.upper() != "SUCCESS":
        res["errors"].append("状态 %s：%s" % (one_line(status, 40), one_line(obj.get("error") or obj.get("response"), 240)))
    if "timed out" in low or "timeout" in status.lower():
        res["timed_out"] = True
    if any(w in low for w in QUOTA_WORDS) and (not obj or status.upper() != "SUCCESS"):
        res["quota_hit"] = True
    if denied and not (obj.get("response") or "").strip():
        res["errors"].append("有动作被权限拦下（%s），这一轮提前结束" % "、".join(res["denied_actions"]))
    raw_last = str(obj.get("response") or "")
    res["last_message"] = one_line(raw_last, 600)
    common.atomic_write_text(runs / ("%s.last.txt" % run_id), redact(raw_last, 4000) + "\n")
    common.atomic_write_json(runs / ("%s.result.json" % run_id), res)
    return res
