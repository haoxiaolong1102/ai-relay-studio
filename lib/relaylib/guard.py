# -*- coding: utf-8 -*-
"""拦截判断：持棒者不是 Claude 时，Claude 只能看不能改。

decide() 返回 (放行?, 给 Claude 的白话理由)。任何异常由调用方按“拦下”处理。
"""
import hashlib
import os
import shlex

from . import common, lock

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
DENY_MSG = "现在是 Codex 在干活，Claude 只能看不能改。要收回请点菜单栏“接力”里的“交回 Claude”。"

# 出现任何一个就整条拒绝（不去判断“是不是写操作”）
BAD_CHARS = set(";&|`$()<>\n\r\\{}")
SIMPLE = {"ls", "cat", "head", "tail", "wc", "stat", "file", "grep", "rg"}
GIT_SUBS = {"status", "log", "show", "diff"}


def bash_readonly(command, git_ok=False):
    """只读白名单。返回 (放行?, 原因)。"""
    if not isinstance(command, str) or not command.strip():
        return False, "空命令"
    bad = sorted(set(command) & BAD_CHARS)
    if bad:
        return False, "命令里有连接/替换/重定向符号"
    try:
        argv = shlex.split(command)
    except ValueError:
        return False, "命令解析不了"
    if not argv:
        return False, "空命令"
    prog, args = argv[0], argv[1:]
    if prog in SIMPLE:
        if prog == "rg" and any(a.startswith("--pre") or a.startswith("--hostname-bin") for a in args):
            return False, "rg 不允许 --pre 这类参数"
        if prog == "file" and any(a in ("-C", "--compile") or (a.startswith("-") and not a.startswith("--") and "C" in a) for a in args):
            return False, "file 不允许 -C"
        if prog == "tail" and any(a in ("-f", "-F", "--follow") or a.startswith("--follow") for a in args):
            return False, "tail 不允许 -f"
        return True, ""
    if prog == "git":
        if not args or args[0] not in GIT_SUBS:
            return False, "git 只允许 status / log / show / diff"
        for a in args[1:]:
            if a == "-c" or a.startswith("--output") or a.startswith("--ext-diff") or a.startswith("--textconv") \
                    or a.startswith("--exec") or a.startswith("--upload-pack"):
                return False, "git 不允许这个参数"
        if not git_ok:
            return False, "工程的 Git 配置在交出后被改过（或没法核对），暂不允许跑 git"
        return True, ""
    if prog in ("relay", str(common.ROOT / "bin" / "relay")):
        if args[:1] == ["status"] and len(args) <= 2 and all(a.replace("-", "").replace("_", "").isalnum() for a in args):
            return True, ""
        return False, "relay 只允许 status"
    return False, "不在只读白名单里"


def _git_config_ok(proj_path, lk):
    """交出时记下的 .git/config 指纹没变，才允许 Claude 这边跑 git（防止借 Git 配置执行命令）。"""
    want = (lk.get("guard_fp") or {}).get("git_config")
    if not want:
        return False
    from . import manifest
    gd = manifest.git_dir(proj_path)
    if not gd:
        return False
    try:
        with open(os.path.join(gd, "config"), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest() == want
    except OSError:
        return False


def git_config_fp(proj_path):
    from . import manifest
    gd = manifest.git_dir(proj_path)
    if not gd:
        return None
    try:
        with open(os.path.join(gd, "config"), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def decide(hook_input, cfg=None):
    """返回 (allow, reason, code)。"""
    cfg = cfg or common.load_config()
    if not cfg.get("live_enabled"):
        return True, "", None
    tool = str(hook_input.get("tool_name") or "")
    ti = hook_input.get("tool_input") or {}
    if not isinstance(ti, dict):
        ti = {}
    cwd = hook_input.get("cwd") or os.getcwd()
    # 涉及的工程：当前目录所在的，加上写入目标所在的
    hits = {}
    for p in (cwd, os.environ.get("CLAUDE_PROJECT_DIR"), ti.get("file_path"), ti.get("notebook_path")):
        if p and isinstance(p, str):
            if not os.path.isabs(p):
                p = os.path.join(cwd, p)
            code, proj = common.find_project(cfg, p)
            if code:
                hits[code] = proj
    if not hits:
        return True, "", None
    for code, proj in hits.items():
        lk = lock.read_lock(code)          # 锁坏了会抛异常 → 调用方拦下
        if lk["holder"] == common.HOLDER_CLAUDE:
            continue
        hname = common.HOLDER_NAMES.get(lk["holder"], lk["holder"])
        deny_msg = "现在是 %s 在干活，Claude 只能看不能改。要收回请点“AI 接力台”里的“收工 / 交回 Claude”。" % hname
        if tool in WRITE_TOOLS:
            return False, deny_msg, code
        if tool == "Bash":
            ok, why = bash_readonly(ti.get("command"), git_ok=_git_config_ok(proj["path"], lk))
            if not ok:
                return False, "%s（这条命令没放行：%s。只读的 ls/cat/head/tail/grep/rg/wc/stat/file、git status/log/show/diff 可以用。）" % (deny_msg, why), code
            continue
        if tool.startswith("mcp__"):
            if tool in (proj.get("readonly_mcp") or []):
                continue
            return False, "%s（外接工具 %s 没登记为只读，先拦下。）" % (deny_msg, tool), code
        # 其它工具（Read、Grep 等）本来就不该进到这里；进来了就放行
    return True, "", None
