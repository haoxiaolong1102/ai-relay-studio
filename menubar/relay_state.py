#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""菜单栏图标用的小帮手：不含交接逻辑，只读现状、拨总开关，全部调用现成的 relaylib / bin/relay。
  relay_state.py json        打印现状（给菜单栏程序读）
  relay_state.py live on|off 开/关总开关（用 bin/relay 里现成的 set_live）
"""
import importlib.machinery
import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.dont_write_bytecode = True
from relaylib import common, lock  # noqa: E402


def state():
    cfg = common.load_config()
    projects = []
    for code, proj in cfg["projects"].items():
        item = {"code": code, "name": proj.get("name") or code, "holder": "unknown",
                "since": "", "segment": None, "running": False}
        try:
            lk = lock.read_lock(code)
            item["holder"] = lk["holder"]
            item["since"] = lk.get("since") or ""
            item["segment"] = lk.get("segment")
        except Exception as e:
            item["error"] = str(e)
        try:
            item["running"] = lock.Busy.peek(code) is not None
        except Exception:
            pass
        projects.append(item)
    try:
        diffs = lock.compare(cfg)
    except Exception as e:
        diffs = [str(e)]
    return {"ok": True, "live": bool(cfg.get("live_enabled")), "projects": projects, "diffs": diffs}


def relay_module():
    path = os.path.join(ROOT, "bin", "relay")
    loader = importlib.machinery.SourceFileLoader("relay_main", path)
    spec = importlib.util.spec_from_loader("relay_main", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def main():
    args = sys.argv[1:]
    try:
        if args[:1] == ["json"]:
            print(json.dumps(state(), ensure_ascii=False))
            return 0
        if args[:1] == ["live"] and args[1:2] in (["on"], ["off"]):
            relay_module().set_live(common.load_config(), args[1] == "on")
            print(json.dumps(state(), ensure_ascii=False))
            return 0
    except Exception as e:
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}, ensure_ascii=False))
        return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
