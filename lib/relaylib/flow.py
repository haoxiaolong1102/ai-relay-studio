# -*- coding: utf-8 -*-
"""交接流程：交给 Codex（handoff）、分段跑、交回 Claude（takeback）。"""
import json
import os
import re
import subprocess
import time

from . import agy, audit, baton, codex, common, gitiso, guard, lock, manifest, media, snapshot, ui
from .common import HOLDER_ANTIGRAVITY, HOLDER_CLAUDE, HOLDER_CODEX, HOLDER_NAMES, RelayError
from .redact import one_line, wrap_untrusted


def stop_flag(code):
    return str(common.STATE / "locks" / ("%s.stop" % code))


def _clear_stop(code):
    # 不删文件，只清空内容
    if os.path.exists(stop_flag(code)):
        common.atomic_write_text(stop_flag(code), "")


def _stop_requested(code):
    try:
        with open(stop_flag(code), encoding="utf-8") as f:
            return f.read().strip() in ("after", "now")
    except OSError:
        return False


def _git_ignored(path, rel):
    p = subprocess.run(["git", "-C", path, "check-ignore", "-q", rel], capture_output=True)
    return p.returncode == 0


def _baseline_changes(code, path, cur):
    """“上一轮结束后到交出这一刻”改了什么（清单对比，权威）。"""
    base = manifest.load(code, "stop-latest")
    if base:
        d = manifest.diff(base, cur)
        d["git"] = []
        return "基线：%s 拍的清单。\n%s" % (base.get("taken"), manifest.summarize(d))
    if manifest.git_dir(path):
        p = subprocess.run(["git", "-C", path, "status", "--porcelain"], capture_output=True, text=True)
        return "没有上一轮的基线清单（第一次交出），改用 git status：\n%s" % (
            "\n".join("  - " + line for line in p.stdout.splitlines()[:60]) or "  工作区干净")
    return "没有上一轮的基线清单（第一次交出），也不是 Git 工程，无法列出此前的变更。"


def run_tests(proj, path):
    """在系统沙箱里跑工程自己的测试命令（禁网、只能写工程和临时目录）。返回 (结果, 摘要)。"""
    cmd = proj.get("test_cmd")
    if not cmd:
        return None, "未配置测试命令"
    profile = ('(version 1)(allow default)(deny network*)(deny file-write*)'
               '(allow file-write* (subpath "%s") (subpath "/private/tmp") (subpath "/private/var/folders")'
               ' (subpath "/dev"))' % path.replace('"', ''))
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    try:
        p = subprocess.run(["/usr/bin/sandbox-exec", "-p", profile] + list(cmd), cwd=path, env=env,
                           capture_output=True, text=True, timeout=int(proj.get("test_timeout_s", 300)))
    except subprocess.TimeoutExpired:
        return "timeout", "测试超时"
    except OSError as e:
        return None, "测试没法在沙箱里启动（%s），已跳过" % e
    tail = one_line((p.stdout + "\n" + p.stderr)[-400:], 300)
    return ("pass" if p.returncode == 0 else "fail"), tail


def handback_status(path):
    text = baton.read_handback(path, 2000) or ""
    m = re.search(r"状态\s*[:：]\s*(完成|未完成|受阻)", text)
    return m.group(1) if m else None


def handoff(cfg, code, by="user", from_chat=False, to=HOLDER_CODEX):
    """无人值守接力：交给 Codex（默认）或 Antigravity，让它自己分段接着做。"""
    if to not in (HOLDER_CODEX, HOLDER_ANTIGRAVITY):
        raise RelayError("不认识这个工具：%s" % to)
    tname = HOLDER_NAMES[to]
    proj = common.project(cfg, code)
    path = common.real(proj["path"])
    name = proj.get("name") or code
    if not cfg.get("live_enabled"):
        raise RelayError("接力总开关现在是关着的。要用请先双击“接力总开关”打开。")
    if not os.path.isdir(path):
        raise RelayError("找不到工程文件夹：%s" % path)
    busy = lock.Busy(code)
    if not busy.acquire({"what": "handoff"}):
        raise RelayError("“%s”的接力正在进行中，不能再开一次。" % name)
    try:
        cur_holder = lock.holder(code)
        if cur_holder != HOLDER_CLAUDE:
            raise RelayError("“%s”现在已经在 %s 手里了，不能重复交出。要收回请双击“交回 Claude”。" % (name, cur_holder))
        if from_chat:
            if ui.ask("要把“%s”交给 %s 接着做吗？" % (name, tname), ["取消", "交给 %s" % tname], "交给 %s" % tname) != "交给 %s" % tname:
                audit.log(code, by, "交出取消", "确认框里点了取消")
                return {"started": False, "message": "已取消，没有交出。"}
        is_git = bool(manifest.git_dir(path))
        if is_git and not _git_ignored(path, "docs/relay/BATON.md"):
            raise RelayError("这个工程的 .gitignore 里没有 docs/relay/，为避免接力记录被提交，先不开始。")
        _clear_stop(code)
        print("① 整理接力棒…", flush=True)
        baton.archive_handback(path)
        cur = manifest.take(path)
        baton.write_fragment(path, "handoff-changes.md", _baseline_changes(code, path, cur))
        baton.rewrite(code, path)
        print("② 素材设只读、拍快照…", flush=True)
        snapshot.protect(cfg, code)
        snap = snapshot.take(cfg, code, "交出前")
        pre = manifest.take(path)
        manifest.save(code, "handoff-pre", pre)
        if to == HOLDER_ANTIGRAVITY:
            if not agy.is_fake() and not os.path.exists(agy.settings(cfg)["bin"]):
                raise RelayError("没找到 Antigravity 的命令行程序（%s），没法交给它。" % agy.settings(cfg)["bin"])
            print("③ 这次接力交给 Antigravity（可上网）。", flush=True)
            if agy.permissions(cfg) == "full":
                print("注意：这次 Antigravity 是“整台电脑全权”（跳过所有权限确认），能改工程文件夹以外的东西。", flush=True)
                audit.log(code, "relay", "权限", "本次接力 Antigravity 为整机全权（跳过权限确认）")
            else:
                print("注意：这次 Antigravity 只能改工程里的文件，跑不了终端命令（配置 antigravity.permissions 不是 full）。", flush=True)
        else:
            if codex.network_allowed(cfg):
                print("③ 这次接力允许 Codex 上网（配置里开着），不做禁网检查。", flush=True)
                audit.log(code, "relay", "联网", "本次接力允许 Codex 上网")
            elif not codex.is_fake(cfg):
                print("③ 检查 Codex 确实不能上网…", flush=True)
                ok, why = net_precheck(cfg)
                if not ok:
                    raise RelayError("禁网检查没通过（%s），为安全起见不开始接力。" % why)
            if codex.sandbox_mode(cfg) == "danger-full-access":
                print("注意：这次 Codex 是“整台电脑全权”（没有沙箱），能改工程文件夹以外的东西。", flush=True)
                audit.log(code, "relay", "权限", "本次接力 Codex 为整机全权（danger-full-access）")
        print("④ 把棒交给 %s…" % tname, flush=True)
        lock.transfer(cfg, code, to, HOLDER_CLAUDE, by, extra={
            "snapshot": snap["id"],
            "iso_pre": gitiso.capture_pre(cfg, code, path, proj.get("raw_dirs", ())) if is_git else None,
            "guard_fp": {"protected": manifest.fingerprint_protected(pre),
                         "git_config": guard.git_config_fp(path)}})
        baton.rewrite(code, path)
        result = _segments(cfg, code, proj, path, is_git, pre, busy, to)
    finally:
        busy.release()
    msg = "“%s”这次接力先到这里：%s\n\n棒还在 %s 手里。要让 Claude 接着做，请点“收工 / 交回 Claude”。" % (name, result["outcome"], tname)
    ui.info(msg)
    return dict(result, started=True, message=msg)


def _segments(cfg, code, proj, path, is_git, pre, busy, to=HOLDER_CODEX):
    tname = HOLDER_NAMES[to]
    runner = agy if to == HOLDER_ANTIGRAVITY else codex
    c = dict(cfg["codex"])
    if to == HOLDER_ANTIGRAVITY:          # 没单独写的时限、段数沿用 Codex 的
        c.update({k: v for k, v in (cfg.get("antigravity") or {}).items()
                  if k in ("max_segments", "segment_timeout_min", "total_timeout_min")})
    max_seg = int(c.get("max_segments", 4))
    seg_timeout = float(c.get("segment_timeout_min", 20)) * 60
    total_timeout = float(c.get("total_timeout_min", 90)) * 60
    raw_dirs = proj.get("raw_dirs", ())
    start = time.time()
    prev, idle, last_test, outcome, segs = pre, 0, None, "", []
    for seg in range(1, max_seg + 1):
        if _stop_requested(code):
            outcome = "你叫停了。"
            break
        if time.time() - start > total_timeout:
            outcome = "到了整次接力的总时限（%d 分钟）。" % (total_timeout // 60)
            break
        lock.update_lock(code, segment=seg)
        audit.log(code, to, "段开始", "第 %d 段" % seg)
        print("⑤ %s 第 %d 段开始（最多 %d 段，每段最长 %d 分钟）…" % (tname, seg, max_seg, seg_timeout // 60), flush=True)
        res = runner.run_segment(
            cfg, code, path, seg, runner.load_prompt(cfg, seg, max_seg), is_git,
            min(seg_timeout, max(30.0, total_timeout - (time.time() - start))), stop_flag(code),
            on_start=lambda pid: busy.write({"what": "handoff", "segment": seg, "child": pid}))
        post = manifest.take(path)
        d = manifest.diff(prev, post, raw_dirs)
        test, test_tail = run_tests(proj, path)
        post = manifest.take(path)            # 测试也可能留下文件，以这份为“Codex 最后状态”
        manifest.save(code, "codex-last", post)
        media_note = media.process_queue(cfg, code, proj, path)
        status = handback_status(path)
        info = {"segment": seg, "seconds": res.get("seconds"), "rc": res["rc"], "usage": res.get("usage"),
                "rate_limits": res.get("rate_limits"), "denials": res["denials"], "commands": res["commands"],
                "changed": len(d["added"]) + len(d["changed"]) + len(d["removed"]), "test": test,
                "test_tail": test_tail, "handback": status, "media": media_note}
        segs.append(info)
        audit.log(code, to, "段结束", "第 %d 段 用时 %ss 改动 %d 项 测试 %s 被拒绝 %d 次 交接说明状态 %s" % (
            seg, res.get("seconds"), info["changed"], test, res["denials"], status))
        problems = []
        if d["raw"]:
            problems.append("素材区被动了：%s" % "、".join(d["raw"][:5]))
        if d["protected"]:
            problems.append("钩子配置区被动了：%s" % "、".join(d["protected"][:5]))
        if d["git"]:
            problems.append("Git 的提交/分支指向变了（%s 不该自己提交）：%s" % (tname, "、".join(d["git"][:5])))
        if problems:
            audit.log(code, "relay", "清单不一致", "；".join(problems))
            outcome = "第 %d 段后发现不该有的变化，已停下：\n- %s\n（可用交出前的快照对照或恢复；不会自动覆盖。）" % (seg, "\n- ".join(problems))
            break
        if res["quota_hit"]:
            outcome = "%s 的额度也用完了（%s）。" % (tname, res["errors"][-1] if res["errors"] else "")
            break
        if res["timed_out"]:
            outcome = "第 %d 段超过了单段时限，已中止这一段。" % seg
            break
        if res["stopped"]:
            outcome = "你叫停了。"
            break
        if res["rc"] != 0:
            outcome = "%s 第 %d 段异常退出（%s）。" % (tname, seg, (res["errors"][-1] if res["errors"] else "退出码 %s" % res["rc"]))
            break
        if res.get("denied_actions") and not res.get("last_message") and info["changed"] == 0:
            outcome = "%s 第 %d 段有动作被权限拦下（%s），什么都没做成就结束了。" % (tname, seg, "、".join(res["denied_actions"]))
            break
        progressed = info["changed"] > 0 or (last_test is not None and test != last_test)
        idle = 0 if progressed else idle + 1
        last_test = test
        if status in ("完成", "受阻"):
            outcome = "%s 报告：%s（共跑了 %d 段%s）。" % (tname, status, seg, "，测试通过" if test == "pass" else ("，测试没过" if test == "fail" else ""))
            break
        if idle >= 2:
            outcome = "连续两段没有任何进展（没改文件、测试结果也没变），已停下。"
            audit.log(code, "relay", "无进展", "连续两段")
            break
        prev = post
    else:
        outcome = "已跑满 %d 段。" % max_seg
    summary = {"outcome": outcome, "segments": segs, "started": common.now_iso()}
    common.atomic_write_json(common.STATE / "runs" / code / "latest-summary.json", summary)
    return summary


def open_session(cfg, code, tool, by="user"):
    """轻量“开工”：用户要亲自到另一个工具（如 Antigravity）里做这个工程。
    只做：整理接力棒、素材设只读、拍快照、存清单、把棒交给那个工具（Claude 这边随即只读）。不启动任何模型。"""
    if tool not in HOLDER_NAMES or tool == HOLDER_CLAUDE:
        raise RelayError("不认识这个工具：%s" % tool)
    tname = HOLDER_NAMES[tool]
    proj = common.project(cfg, code)
    path = common.real(proj["path"])
    name = proj.get("name") or code
    if not cfg.get("live_enabled"):
        raise RelayError("接力总开关现在是关着的。要用请先打开总开关。")
    if not os.path.isdir(path):
        raise RelayError("找不到工程文件夹：%s" % path)
    busy = lock.Busy(code)
    if not busy.acquire({"what": "open"}):
        raise RelayError("“%s”的接力正在进行中，不能再开一次。" % name)
    try:
        cur_holder = lock.holder(code)
        if cur_holder != HOLDER_CLAUDE:
            raise RelayError("“%s”现在已经在 %s 手里了。要先收工/交回 Claude，才能再开工。" % (
                name, HOLDER_NAMES.get(cur_holder, cur_holder)))
        is_git = bool(manifest.git_dir(path))
        if is_git and not _git_ignored(path, "docs/relay/BATON.md"):
            raise RelayError("这个工程的 .gitignore 里没有 docs/relay/，为避免接力记录被提交，先不开始。")
        _clear_stop(code)
        print("① 整理接力棒…", flush=True)
        baton.archive_handback(path)
        cur = manifest.take(path)
        baton.write_fragment(path, "handoff-changes.md", _baseline_changes(code, path, cur))
        baton.rewrite(code, path)
        print("② 素材设只读、拍快照…", flush=True)
        snapshot.protect(cfg, code)
        snap = snapshot.take(cfg, code, "开工前")
        pre = manifest.take(path)
        manifest.save(code, "handoff-pre", pre)
        print("③ 登记：现在由 %s 来做…" % tname, flush=True)
        lock.transfer(cfg, code, tool, HOLDER_CLAUDE, by, extra={
            "mode": "manual", "snapshot": snap["id"],
            "iso_pre": gitiso.capture_pre(cfg, code, path, proj.get("raw_dirs", ())) if is_git else None,
            "guard_fp": {"protected": manifest.fingerprint_protected(pre),
                         "git_config": guard.git_config_fp(path)}})
        baton.rewrite(code, path)
    finally:
        busy.release()
    msg = ("“%s”已开工，开工前的样子已拍了快照（%s）。\n\n现在可以去 %s 里打开这个文件夹干活：\n%s\n\n"
           "这期间 Claude 在这个工程里只能看、不能改。做完后回到菜单栏“接力”点“收工 / 交回 Claude”。" % (
               name, snap["id"], tname, path))
    ui.info(msg)
    return {"started": True, "message": msg, "snapshot": snap["id"]}


def takeback(cfg, code, by="user"):
    proj = common.project(cfg, code)
    path = common.real(proj["path"])
    name = proj.get("name") or code
    if lock.holder(code) == HOLDER_CLAUDE:
        raise RelayError("“%s”本来就在 Claude 手里，不用交回。" % name)
    running = lock.Busy.peek(code)
    if running is not None:
        choice = ui.ask("接手的工具还在跑（第 %s 段）。怎么办？" % running.get("segment", "?"),
                        ["先不交回", "等这一段跑完", "现在叫停"], "等这一段跑完")
        if choice == "先不交回":
            return {"done": False, "message": "没有交回，Codex 继续。"}
        common.atomic_write_text(stop_flag(code), "now" if choice == "现在叫停" else "after")
        audit.log(code, by, "请求停止", choice)
        print("等 Codex 停下…", flush=True)
        deadline = time.time() + float(cfg["codex"].get("segment_timeout_min", 20)) * 60 + 120
        while lock.Busy.peek(code) is not None:
            if time.time() > deadline:
                raise RelayError("等了很久 Codex 还没停下。请稍后再双击一次“交回 Claude”。")
            time.sleep(1)
    busy = lock.Busy(code)
    if not busy.acquire({"what": "takeback"}):
        raise RelayError("“%s”的接力正在进行中，稍后再试。" % name)
    try:
        lk = lock.read_lock(code)
        tool = lk["holder"]
        tname = HOLDER_NAMES.get(tool, tool)
        manual = lk.get("mode") == "manual"
        cur = manifest.take(path)
        raw_dirs = proj.get("raw_dirs", ())
        warnings = []
        last = None if manual else manifest.load(code, "codex-last")
        if last:
            d = manifest.diff(last, cur, raw_dirs)
            if not manifest.is_empty(d):
                warnings.append("Codex 最后一段结束之后，又有人改了文件（可能是别的应用，或没拿棒就写了）：\n" + manifest.summarize(d, 10))
        fp = (lk.get("guard_fp") or {}).get("protected")
        if fp and manifest.fingerprint_protected(cur) != fp:
            pre0 = manifest.load(code, "handoff-pre") or {}
            dd = manifest.diff(pre0, cur, raw_dirs)
            warnings.append("工程里的钩子配置（.claude/、Git 钩子或 Git 配置）在接力期间被改过：\n" + "\n".join("  - " + x for x in dd["protected"][:10]))
        pre = manifest.load(code, "handoff-pre")
        total = manifest.diff(pre, cur, raw_dirs) if pre else None
        if total and total["raw"]:
            warnings.append("素材区有变化：\n" + "\n".join("  - " + x for x in total["raw"][:10]))
        if total and total["git"]:
            warnings.append("Git 的提交/分支指向在接力期间变了（%s 不该自己在当前分支上提交）：" % tname + "\n" + "\n".join("  - " + x for x in total["git"][:10]))
        if warnings:
            audit.log(code, "relay", "清单不一致", " | ".join(w.splitlines()[0] for w in warnings))
            if ui.ask("交回前发现下面的情况：\n\n%s\n\n交出前的快照还在，可以用它对照。要怎么办？" % "\n\n".join(warnings),
                      ["先不交回", "我知道了，仍然交回"], "先不交回") != "我知道了，仍然交回":
                return {"done": False, "message": "没有交回。棒还在 %s 手里，工程没有被改动。" % tname, "warnings": warnings}
            audit.log(code, by, "带着警告交回", "用户确认")
        print("拍交回前的快照…", flush=True)
        snap = snapshot.take(cfg, code, "交回前")
        summary = {} if manual else (common.read_json(common.STATE / "runs" / code / "latest-summary.json", {}) or {})
        lines = ["- 接手的工具：%s（%s）" % (tname, "用户亲自在那边操作" if manual else "无人值守分段跑"),
                 "- 交出：%s；交回：%s" % (lk.get("since"), common.now_iso()),
                 "- 结果：%s" % (summary.get("outcome") or ("（用户亲自操作，没有运行记录）" if manual else "（没有运行记录）"))]
        for s in summary.get("segments", []):
            lines.append("- 第 %s 段：用时 %s 秒，改动 %s 项，测试 %s，交接说明状态 %s" % (
                s["segment"], s.get("seconds"), s.get("changed"), s.get("test"), s.get("handback")))
        lines += ["", "### 接力期间的文件变更（清单对比，以此为准）", manifest.summarize(total) if total else "没有交出时的清单，无法对比。"]
        if warnings:
            lines += ["", "### 交回时的警告", "\n".join(warnings)]
        hb = baton.read_handback(path)
        lines += ["", "### %s 写的交接说明（HANDBACK.md）" % tname, wrap_untrusted(hb) if hb else "%s 没有写交接说明。" % tname]
        lines += ["", "- 交出前快照：%s；交回前快照：%s" % (lk.get("snapshot"), snap["id"])]
        iso = None
        if manifest.git_dir(path) and gitiso.settings(cfg)["isolated_commit"]:
            print("把这段时间的改动记到隔离分支…", flush=True)
            iso = gitiso.publish(cfg, code, path, tool, lk.get("since"), lk.get("snapshot"), raw_dirs, lk.get("iso_pre"))
            lines += gitiso.describe(iso)
        baton.write_fragment(path, "period.md", "\n".join(lines))
        lock.transfer(cfg, code, HOLDER_CLAUDE, lk["holder"], by)
        manifest.save(code, "stop-latest", manifest.take(path, light=True))
        baton.rewrite(code, path)
        _clear_stop(code)
    finally:
        busy.release()
    msg = "“%s”已交回 Claude。\n\n现在回到 Claude，在这个工程的对话里说：接回来" % name
    if iso and iso.get("branch"):
        msg += "\n\n%s 这段时间的改动另存了一个隔离分支：%s（%s）。当前分支没动。" % (
            tname, iso["branch"], "已推到 GitHub/远程" if iso.get("pushed") else "只在本机，没推送")
    ui.info(msg)
    return {"done": True, "message": msg, "warnings": warnings, "isolated": iso}


# ---------- 禁网预检（真模型，步骤 12 的第一道闸门） ----------

def net_precheck(cfg, force=False):
    """用和正式接力完全相同的沙箱参数让 Codex 访问一个无害地址，必须失败才算过。
    结果按“Codex 版本 + 参数”缓存。返回 (过?, 说明)。"""
    if codex.network_allowed(cfg):
        return True, "配置里已允许 Codex 上网，不需要做禁网检查"
    key = json.dumps([codex.version(cfg), codex.sandbox_args(cfg)], ensure_ascii=False)
    cache_file = common.STATE / "precheck-net.json"
    cache = common.read_json(cache_file, {}) or {}
    if not force and cache.get("key") == key:
        return bool(cache.get("ok")), cache.get("why", "")
    work = common.ensure_private_dir(common.STATE / "precheck-work")
    prompt = ("这是一次联网隔离自检。请只做一件事：在终端里执行 `curl -sS -m 8 -o /dev/null -w '%{http_code}' https://example.com`，"
              "然后用一句话报告命令的退出码。不要做任何别的事，不要修改任何文件。")
    res = codex.run_segment(cfg, "_precheck", str(work), 0, prompt, False, 180)
    cmds = []
    for name in sorted(os.listdir(str(common.STATE / "runs" / "_precheck"))):
        if name == res["run_id"] + ".jsonl":
            with open(common.STATE / "runs" / "_precheck" / name, encoding="utf-8") as f:
                for line in f:
                    ev = json.loads(line)
                    if "cmd" in ev and "exit_code" in ev and ("curl" in ev["cmd"] or "example.com" in ev["cmd"]):
                        cmds.append(ev)
    if res["quota_hit"]:
        return False, "Codex 额度不够，没法做检查"
    if not cmds:
        ok, why = False, "没看到 Codex 执行访问命令，无法判定"
    elif all(ev["exit_code"] != 0 for ev in cmds):
        ok, why = True, "访问被拒（退出码 %s），禁网有效" % cmds[-1]["exit_code"]
    else:
        ok, why = False, "Codex 居然访问成功了，禁网参数没生效"
    common.atomic_write_json(cache_file, {"key": key, "ok": ok, "why": why, "checked": common.now_iso()})
    audit.log(None, "relay", "禁网预检", "%s：%s" % ("通过" if ok else "没过", why))
    return ok, why
