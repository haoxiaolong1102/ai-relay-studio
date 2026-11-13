#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""记录类钩子（PostToolUse / Stop / StopFailure）。永远放行：任何情况都以退出码 0 结束。
用法：hook_baton.py append | rewrite [--failed]"""
import os
import sys


def main():
    import json
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
    from relaylib import audit, baton, common, lock, manifest
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    failed = "--failed" in sys.argv
    data = json.loads(sys.stdin.read() or "{}")
    cfg = common.load_config()
    if not cfg.get("live_enabled"):
        return
    cwd = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()
    code, proj = common.find_project(cfg, cwd)
    if not code:
        code, proj = common.find_project(cfg, data.get("cwd"))
    if not code:
        return
    if lock.holder(code) != common.HOLDER_CLAUDE:
        audit.log(code, "claude", "非持棒时的活动", "%s %s" % (mode, data.get("tool_name") or ""))
        return
    if mode == "append":
        baton.append_tool(proj["path"], data)
    elif mode == "rewrite":
        baton.append_stop(proj["path"], data, failed=failed)
        baton.rewrite(code, proj["path"])
        # 每轮结束拍一份轻量清单，作为“下一轮改了什么”的基线
        manifest.save(code, "stop-latest", manifest.take(proj["path"], light=True))


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        pass
    sys.exit(0)
