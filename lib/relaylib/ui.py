# -*- coding: utf-8 -*-
"""弹窗（系统自带的选择框）。自测时设 RELAY_UI_ANSWERS=<json 文件>，按顺序取答案，不弹窗。"""
import json
import os
import subprocess

TITLE = "AI 接力"


class NoAnswer(Exception):
    pass


def _auto():
    path = os.environ.get("RELAY_UI_ANSWERS")
    if not path:
        return None
    with open(path, encoding="utf-8") as f:
        answers = json.load(f)
    if not answers:
        raise NoAnswer("自测答案用完了")
    first = answers.pop(0)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(answers, f, ensure_ascii=False)
    return first


def _q(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _osascript(script):
    p = subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, text=True)
    return p.returncode, p.stdout.strip()


def info(msg):
    """只有一个“好”按钮的提示。"""
    print("\n【提示】%s\n" % msg, flush=True)
    if os.environ.get("RELAY_UI_ANSWERS") or os.environ.get("RELAY_NO_POPUP"):
        return
    _osascript('display dialog %s with title %s buttons {"好"} default button 1' % (_q(msg[:1500]), _q(TITLE)))


def ask(msg, buttons, default=None):
    """让用户在两三个按钮里选一个。关掉窗口/出错＝第一个按钮（约定第一个是“取消/先不”）。"""
    print("\n【请选择】%s\n  选项：%s" % (msg, " / ".join(buttons)), flush=True)
    if os.environ.get("RELAY_UI_ANSWERS"):
        ans = _auto()
        print("  （自测自动回答：%s）" % ans, flush=True)
        return ans if ans in buttons else buttons[0]
    script = 'button returned of (display dialog %s with title %s buttons {%s} default button %s)' % (
        _q(msg[:1500]), _q(TITLE), ", ".join(_q(b) for b in buttons), _q(default or buttons[0]))
    rc, out = _osascript(script)
    return out if rc == 0 and out in buttons else buttons[0]


def choose(prompt, options):
    """从列表里选一项；取消返回 None。"""
    print("\n【请选择】%s\n  选项：%s" % (prompt, " / ".join(options)), flush=True)
    if os.environ.get("RELAY_UI_ANSWERS"):
        ans = _auto()
        print("  （自测自动回答：%s）" % ans, flush=True)
        return ans if ans in options else None
    script = 'choose from list {%s} with title %s with prompt %s' % (
        ", ".join(_q(o) for o in options), _q(TITLE), _q(prompt))
    rc, out = _osascript(script)
    return out if rc == 0 and out in options else None
