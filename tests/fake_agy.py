#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""假 Antigravity 命令行（自测用，不调任何模型）。场景由环境变量 FAKE_AGY_SCENARIO 决定：ok partial denied quota。
在当前目录（工程）里干活，最后打一行 JSON，和真的 agy -p --output-format json 一样。"""
import json
import os
import sys
import time

scen = os.environ.get("FAKE_AGY_SCENARIO", "ok")
args_out = os.environ.get("FAKE_AGY_ARGS_OUT")
if args_out:
    with open(args_out, "w", encoding="utf-8") as f:
        json.dump({"argv": sys.argv[1:], "cwd": os.getcwd()}, f, ensure_ascii=False)


def w(rel, text, mode="w"):
    os.makedirs(os.path.dirname(rel) or ".", exist_ok=True)
    with open(rel, mode, encoding="utf-8") as f:
        f.write(text)


def done(status="SUCCESS", response="", denied=(), rc=0):
    print(json.dumps({"conversation_id": "fake-agy-1", "status": status, "response": response,
                      "duration_seconds": 0.1, "num_turns": 1,
                      "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                      "denied_actions": [{"action": a, "display_name": a} for a in denied]}, ensure_ascii=False))
    sys.exit(rc)


if scen == "ok":
    w("src/main.py", "print('by fake agy')\n", "a")
    w("docs/relay/HANDBACK.md", "状态：完成\n做了第 1 项。改了 src/main.py。密钥 sk-ant-FAKEFAKEFAKEFAKEFAKE1234 不该出现。\n")
    done(response="做完了第 1 项，密钥 sk-ant-FAKEFAKEFAKEFAKEFAKE1234")
elif scen == "partial":
    w("src/main.py", "# step %s\n" % time.time(), "a")
    w("docs/relay/HANDBACK.md", "状态：未完成\n还差一点。\n")
    done(response="还没做完")
elif scen == "denied":
    print('jetski: no output produced — a tool required the "command" permission that headless mode cannot prompt for, so it was auto-denied.')
    done(response="", denied=["command"])
elif scen == "quota":
    print("Error: RESOURCE_EXHAUSTED: quota exceeded for this model, try again later")
    sys.exit(1)
done()
