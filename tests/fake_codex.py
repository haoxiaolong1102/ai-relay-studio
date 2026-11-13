#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""假 Codex：参数形态和真 codex 相同，按环境变量 FAKE_CODEX_SCENARIO 行事。不调用任何模型。
场景：ok partial noop touch_raw new_raw touch_hooks git_commit hang leak queue_media quota
FAKE_CODEX_NO_ESCAPE=1：touch_raw/new_raw 遇到只读就老实失败（不模拟“越狱”）。
FAKE_CODEX_LEAK_LAST=1：leak 场景里最后一句话也带假密钥。
FAKE_CODEX_OUTSIDE：queue_media 场景里“工程外文件”的绝对路径。"""
import json
import os
import subprocess
import sys
import time

FAKE_KEY = "sk-FAKEFAKEFAKEFAKEFAKE1234"
FAKE_ENV = "API_TOKEN=supersecretvalue123"


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def cmd_event(command, output="", code=0):
    emit({"type": "item.completed", "item": {"type": "command_execution", "command": command,
                                             "aggregated_output": output, "exit_code": code}})


def handback(wd, status, body="做了点事。"):
    d = os.path.join(wd, "docs", "relay")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "HANDBACK.md"), "w", encoding="utf-8") as f:
        f.write("状态：%s\n\n%s\n" % (status, body))


def edit_plain(wd):
    p = os.path.join(wd, "src", "main.py")
    with open(p, "a", encoding="utf-8") as f:
        f.write("# edited by fake codex %d\n" % time.time_ns())
    cmd_event("apply_patch src/main.py", "patched", 0)


def raw_write(wd, new):
    """在 raw/ 里改文件或新建文件；只读时先老实失败，再（除非禁止）模拟越狱解锁后重试。"""
    raw = os.path.join(wd, "raw")
    target = os.path.join(raw, "new_by_codex.txt" if new else "notes.txt")

    def attempt():
        with open(target, "a", encoding="utf-8") as f:
            f.write("tampered %d\n" % time.time_ns())

    try:
        attempt()
        cmd_event("echo tampered >> raw", "", 0)
        return True
    except OSError as e:
        cmd_event("echo tampered >> raw", "Operation not permitted: %s" % e, 1)
    if os.environ.get("FAKE_CODEX_NO_ESCAPE"):
        return False
    subprocess.run(["chflags", "-R", "nouchg", raw])
    subprocess.run(["chmod", "-R", "u+w", raw])
    attempt()
    cmd_event("chflags -R nouchg raw && echo tampered >> raw", "", 0)
    return True


def main():
    argv = sys.argv[1:]
    if argv and argv[0] == "--version":
        print("codex-cli 0.0.0-fake")
        return 0
    if not argv or argv[0] != "exec":
        sys.stderr.write("fake codex: 只支持 exec / --version\n")
        return 2
    wd = out = prompt = None
    i = 1
    while i < len(argv):
        a = argv[i]
        if a in ("-C", "-s", "-m", "-c", "-o"):
            val = argv[i + 1]
            if a == "-C":
                wd = val
            elif a == "-o":
                out = val
            i += 2
        elif a in ("--json", "--skip-git-repo-check"):
            i += 1
        else:
            prompt = a
            i += 1
    if not wd or not out or prompt is None:
        sys.stderr.write("fake codex: 缺少 -C / -o / prompt\n")
        return 2
    os.chdir(wd)
    scen = os.environ.get("FAKE_CODEX_SCENARIO", "ok")
    last = "完成了。"
    emit({"type": "thread.started", "thread_id": "fake-1"})
    emit({"type": "turn.started"})

    if scen == "ok":
        edit_plain(wd)
        handback(wd, "完成")
    elif scen == "partial":
        edit_plain(wd)
        handback(wd, "未完成", "还没做完。")
        last = "还没做完。"
    elif scen == "noop":
        cmd_event("ls", "a.txt", 0)
        handback(wd, "未完成", "什么也没改。")
        last = "什么也没改。"
    elif scen in ("touch_raw", "new_raw"):
        raw_write(wd, scen == "new_raw")
        handback(wd, "未完成")
    elif scen == "touch_hooks":
        os.makedirs(os.path.join(wd, ".claude"), exist_ok=True)
        with open(os.path.join(wd, ".claude", "settings.json"), "w", encoding="utf-8") as f:
            f.write('{"hooks": {"tampered": true}}\n')
        cmd_event("echo > .claude/settings.json", "", 0)
        handback(wd, "未完成")
    elif scen == "git_commit":
        edit_plain(wd)
        subprocess.run(["git", "-C", wd, "add", "-A"])
        subprocess.run(["git", "-C", wd, "commit", "-q", "-m", "fake commit by codex"])
        cmd_event("git commit -am fake", "", 0)
        handback(wd, "完成")
    elif scen == "hang":
        time.sleep(600)
    elif scen == "leak":
        cmd_event("echo %s %s" % (FAKE_KEY, FAKE_ENV),
                  "AGGREGATED-BODY-MARKER %s %s" % (FAKE_KEY, FAKE_ENV), 0)
        edit_plain(wd)
        handback(wd, "完成")
        if os.environ.get("FAKE_CODEX_LEAK_LAST"):
            last = "最后一句 %s %s" % (FAKE_KEY, FAKE_ENV)
    elif scen == "queue_media":
        edit_plain(wd)
        qd = os.path.join(wd, "docs", "relay", "media_requests")
        os.makedirs(qd, exist_ok=True)
        outside = os.environ.get("FAKE_CODEX_OUTSIDE", "/nonexistent/outside.wav")
        link = os.path.join(wd, "link_to_outside.wav")
        if not os.path.lexists(link):
            os.symlink(outside, link)
        reqs = [("1-outside.json", outside), ("2-symlink.json", link),
                ("3-legal.json", os.path.join(wd, "audio", "ok.wav"))]
        for name, f in reqs:
            with open(os.path.join(qd, name), "w", encoding="utf-8") as fh:
                json.dump({"file": f, "question": "这段音频讲了什么？", "deep": False, "route": None}, fh)
        handback(wd, "完成")
    elif scen == "quota":
        emit({"type": "error", "message": "You've hit your usage limit. Upgrade or try again at 00:47."})
        return 1
    else:
        sys.stderr.write("fake codex: 未知场景 %s\n" % scen)
        return 2

    emit({"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 20}})
    with open(out, "w", encoding="utf-8") as f:
        f.write(last)
    return 0


if __name__ == "__main__":
    sys.exit(main())
