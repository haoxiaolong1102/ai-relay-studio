#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""只记录的 StopFailure 探针：往 state/probe/stopfailure.jsonl 追加一行，别的什么都不做。
永远以退出码 0 结束。"""
import os
import sys


def main():
    import json
    import time
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
    from relaylib import common
    from relaylib.redact import one_line
    try:
        if not common.load_config().get("live_enabled"):
            return
    except Exception:
        return
    data = json.loads(sys.stdin.read() or "{}")
    rec = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "event": one_line(data.get("hook_event_name"), 40),
        "error": one_line(data.get("error"), 80),
        "error_details": one_line(data.get("error_details"), 500),
        "last_message": one_line(data.get("last_assistant_message"), 300),
        "cwd": one_line(data.get("cwd"), 300),
        "session": one_line(data.get("session_id"), 80),
    }
    d = common.ensure_private_dir(common.STATE / "probe")
    fd = os.open(str(d / "stopfailure.jsonl"), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8"))
    finally:
        os.close(fd)


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        pass
    sys.exit(0)
