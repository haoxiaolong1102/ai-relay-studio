# -*- coding: utf-8 -*-
"""隔离分支：把另一个工具（Codex / Antigravity）在接力期间的改动，记成一个单独分支上的提交，并可推到 GitHub。

原则（隔离、能溯源、能重返）：
- 隔离：只写 refs/heads/relay/<工具>/<时间>，绝不动当前分支、当前索引、工作区；推送只推这一个分支，不强推。
- 溯源：提交的作者写明是哪个工具，说明里写交出时间、起点提交、交出前快照号。
- 重返：提交的父提交就是交出那一刻的 HEAD；另有交出前快照。
做法：用一个临时索引文件拼出“现在工作区的样子”，commit-tree 出一个提交，再 update-ref。
提交和推送都由接力程序做，工具自己不需要、也拿不到推送权限。
配置在 projects.json 的 "git"：{"isolated_commit": true, "push": true, "remote": "origin", "max_file_mb": 50}；没写＝全关。
"""
import os
import subprocess

from . import audit, common

TOOL_NAMES = {"codex": "Codex", "antigravity": "Antigravity"}


def settings(cfg):
    g = cfg.get("git") or {}
    return {"isolated_commit": bool(g.get("isolated_commit", False)), "push": bool(g.get("push", False)),
            "remote": g.get("remote") or "origin", "max_file_mb": float(g.get("max_file_mb", 50))}


def _git(path, args, env=None, timeout=120, inp=None):
    e = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    if env:
        e.update(env)
    return subprocess.run(["git", "-C", path] + list(args), capture_output=True, text=True,
                          timeout=timeout, env=e, input=inp)


def head(path):
    p = _git(path, ["rev-parse", "-q", "--verify", "HEAD"])
    return p.stdout.strip() if p.returncode == 0 else None


def _worktree_tree(cfg, code, path, raw_dirs):
    """用临时索引拼出“工作区现在的样子”，返回 (tree, 没收进来的大文件, 出错说明)。不动真正的索引。"""
    st = settings(cfg)
    tmpdir = common.ensure_private_dir(common.STATE / "tmp")
    index = str(tmpdir / ("iso-%s-%s-%d.index" % (code, common.stamp(), os.getpid())))
    env = {"GIT_INDEX_FILE": index}
    try:
        p = _git(path, ["read-tree", "HEAD"], env)
        if p.returncode != 0:
            return None, [], "读不了当前提交（%s）" % p.stderr.strip()[:200]
        # 不收：登记的素材目录、过大的新文件（避免把几个 G 的素材塞进 Git）
        skipped = []
        limit = st["max_file_mb"] * 1e6
        for rel in _git(path, ["ls-files", "-o", "--exclude-standard", "-z"]).stdout.split("\0"):
            if not rel:
                continue
            try:
                if os.path.getsize(os.path.join(path, rel)) > limit:
                    skipped.append(rel)
            except OSError:
                pass
        # 素材目录要是已经被 .gitignore 忽略了，就不用（也不能）再写进排除清单，否则 git 会报错
        raws = [d.strip("/") for d in raw_dirs if d and
                _git(path, ["check-ignore", "-q", d.strip("/")]).returncode != 0]
        spec = ["."] + [":(exclude,top)%s" % d for d in raws] + \
               [":(exclude,top,literal)%s" % s for s in skipped]
        p = _git(path, ["add", "-A", "--"] + spec, env, timeout=600)
        if p.returncode != 0:
            return None, skipped, "整理改动时出错（%s）" % p.stderr.strip()[:200]
        return _git(path, ["write-tree"], env).stdout.strip() or None, skipped, ""
    finally:
        for f in (index, index + ".lock"):
            if os.path.exists(f):
                os.unlink(f)   # 自己刚建的临时索引文件


def _tree_of(path, rev):
    return _git(path, ["rev-parse", "%s^{tree}" % rev]).stdout.strip()


def capture_pre(cfg, code, path, raw_dirs=()):
    """交出/开工那一刻：如果工作区里已经有没提交的改动，先记成一个“开工前”的提交（不挂分支），返回它的编号。
    这样之后工具改的东西和原先就有的改动分得开。没有改动、没开功能、出错都返回 None。"""
    if not settings(cfg)["isolated_commit"]:
        return None
    try:
        base = head(path)
        if not base:
            return None
        tree, _, err = _worktree_tree(cfg, code, path, raw_dirs)
        if not tree or tree == _tree_of(path, base):
            return None
        who = {"GIT_AUTHOR_NAME": "交出前已有的改动", "GIT_AUTHOR_EMAIL": "pre@relay.local",
               "GIT_COMMITTER_NAME": "AI 接力程序", "GIT_COMMITTER_EMAIL": "relay@relay.local"}
        msg = "接力：交出那一刻工作区里已有、还没提交的改动（不是接手工具做的）\n\n工程：%s\n时间：%s\n" % (code, common.now_iso())
        p = _git(path, ["commit-tree", tree, "-p", base], who, inp=msg)
        return p.stdout.strip() or None
    except Exception as e:
        audit.log(code, "relay", "隔离分支出错", "记录开工前状态失败（%s）" % e)
        return None


def publish(cfg, code, path, tool, since, snapshot_id=None, raw_dirs=(), pre_commit=None):
    """返回 {"branch", "commit", "base", "pushed", "note"}；什么都没做时 branch 为 None，note 说明原因。
    pre_commit：capture_pre 记下的“开工前”提交；有的话工具的提交接在它后面。
    任何失败都不抛异常（不能挡住交回），只写进 note。"""
    st = settings(cfg)
    res = {"branch": None, "commit": None, "base": None, "pushed": False, "note": ""}
    if not st["isolated_commit"]:
        res["note"] = "配置里没开隔离分支"
        return res
    try:
        base = head(path)
        if not base:
            res["note"] = "这个工程还没有任何提交，没法建隔离分支"
            return res
        res["base"] = base
        parent = base
        if pre_commit and _git(path, ["cat-file", "-e", pre_commit + "^{commit}"]).returncode == 0:
            parent = pre_commit
        tree, skipped, err = _worktree_tree(cfg, code, path, raw_dirs)
        if err:
            res["note"] = err
            return res
        if not tree or tree == _tree_of(path, parent):
            res["note"] = "接手期间没有可记录的改动，没建分支"
            return res
        # 工作区的样子和某个已有的隔离分支完全一样（这段时间其实没人改东西）→ 不重复建
        for line in _git(path, ["for-each-ref", "--format=%(refname:short) %(tree)", "refs/heads/relay"]).stdout.splitlines():
            old_branch, _, old_tree = line.partition(" ")
            if old_tree == tree:
                res["note"] = "这段时间没有新改动（内容和已有的隔离分支 %s 一样），没建新分支" % old_branch
                return res
        name = TOOL_NAMES.get(tool, tool)
        msg ="接力：%s 在 %s 至 %s 期间的改动（隔离分支，未合并）\n\n工程：%s\n起点提交：%s\n交出前快照：%s\n" % (
            name, since or "?", common.now_iso(), code, base, snapshot_id or "无")
        if skipped:
            msg += "没收进来的大文件（超过 %d MB）：\n%s\n" % (st["max_file_mb"], "\n".join("  " + s for s in skipped[:50]))
        who = {"GIT_AUTHOR_NAME": "%s（经 AI 接力）" % name, "GIT_AUTHOR_EMAIL": "%s@relay.local" % tool,
               "GIT_COMMITTER_NAME": "AI 接力程序", "GIT_COMMITTER_EMAIL": "relay@relay.local"}
        p = _git(path, ["commit-tree", tree, "-p", parent], who, inp=msg)
        commit = p.stdout.strip()
        if p.returncode != 0 or not commit:
            res["note"] = "建提交时出错（%s）" % p.stderr.strip()[:200]
            return res
        branch = "relay/%s/%s" % (tool, common.stamp())
        ref = "refs/heads/" + branch
        p = _git(path, ["update-ref", ref, commit, ""])      # 最后的 "" ＝ 这个分支必须原先不存在
        if p.returncode != 0:
            res["note"] = "建分支时出错（%s）" % p.stderr.strip()[:200]
            return res
        res.update(branch=branch, commit=commit, parent=parent)
        audit.log(code, "relay", "隔离分支", "%s @ %s（起点 %s）" % (branch, commit[:12], base[:12]))
        if skipped:
            res["note"] = "有 %d 个大文件没收进分支" % len(skipped)
        if st["push"]:
            remotes = _git(path, ["remote"]).stdout.split()
            if st["remote"] not in remotes:
                res["note"] = (res["note"] + "；" if res["note"] else "") + "这个工程没有名为 %s 的远程仓库，只在本机建了分支，没推送" % st["remote"]
            else:
                p = _git(path, ["push", st["remote"], "%s:%s" % (ref, ref)], timeout=300)
                if p.returncode == 0:
                    res["pushed"] = True
                    audit.log(code, "relay", "推送", "%s → %s" % (branch, st["remote"]))
                else:
                    why = (p.stderr.strip().splitlines() or ["?"])[-1][:200]
                    res["note"] = (res["note"] + "；" if res["note"] else "") + "推送没成功（%s），分支留在本机" % why
                    audit.log(code, "relay", "推送失败", "%s：%s" % (branch, why))
    except Exception as e:   # 兜底：绝不挡住交回
        res["note"] = "隔离分支这一步出错（%s: %s）" % (type(e).__name__, e)
        audit.log(code, "relay", "隔离分支出错", res["note"])
    return res


def describe(res):
    """给接力棒和弹窗用的白话几行。"""
    if not res.get("branch"):
        return ["- 隔离分支：没建（%s）" % (res.get("note") or "未知原因")]
    lines = ["- 隔离分支：`%s`（提交 %s，起点 %s）；%s" % (
        res["branch"], res["commit"][:12], res["base"][:12],
        "已推到远程仓库" if res.get("pushed") else "只在本机，没推送")]
    if res.get("note"):
        lines.append("  - 注意：%s" % res["note"])
    lines.append("  - 看它改了什么：`git diff %s %s`；当前分支和工作区没被动过。" % ((res.get("parent") or res["base"])[:12], res["branch"]))
    if res.get("parent") and res["parent"] != res["base"]:
        lines.append("  - 交出时工作区里已有的未提交改动单独记在提交 %s（分支上的前一个提交），不算在接手工具头上。" % res["parent"][:12])
    return lines
