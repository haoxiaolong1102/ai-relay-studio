#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""PreToolUse 拦截钩子。放行＝退出码 0；拦下＝退出码 2（理由写到 stderr）。
兜底：任何异常、3 秒看门狗到点，都以退出码 2 结束（宁可多拦）。"""
import os
import signal
import sys

FAIL_MSG = "接力系统的拦截检查出了问题（%s），为安全起见先拦下这次操作。请点菜单栏“接力”里的“接力总开关”查看。"


def _die(why):
    try:
        sys.stderr.write(FAIL_MSG % why + "\n")
        sys.stderr.flush()
    finally:
        os._exit(2)


def _watchdog(signum, frame):
    _die("检查超时")


def main():
    signal.signal(signal.SIGALRM, _watchdog)
    signal.setitimer(signal.ITIMER_REAL, float(os.environ.get("RELAY_GUARD_WATCHDOG", "3")))
    try:
        import json
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
        from relaylib import audit, guard
        if os.environ.get("RELAY_TEST_GUARD_HANG"):      # 只给自测用：模拟卡死
            import time
            time.sleep(60)
        data = json.loads(sys.stdin.read() or "{}")
        if not isinstance(data, dict):
            raise ValueError("钩子输入不是对象")
        allow, reason, code = guard.decide(data)
        if allow:
            os._exit(0)
        ti = data.get("tool_input") or {}
        what = ti.get("file_path") or ti.get("notebook_path") or ti.get("command") or ""
        audit.log(code, "claude", "拦截", "%s%s %s" % (
            data.get("tool_name"), "（子代理）" if data.get("agent_id") else "", what))
        sys.stderr.write(reason + "\n")
        sys.stderr.flush()
        os._exit(2)
    except SystemExit:
        raise
    except BaseException as e:  # noqa
        _die(type(e).__name__)


if __name__ == "__main__":
    main()
