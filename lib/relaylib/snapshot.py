# -*- coding: utf-8 -*-
"""交接前快照、恢复（只解到新文件夹）、素材只读。

可信快照只认工程外的压缩包 / rsync 副本；refs/relay-snap 只当便利。
"""
import hashlib
import os
import shutil
import stat
import subprocess
import tempfile

from . import audit, common, manifest
from .common import RelayError


def _run(cmd, cwd=None, env=None, timeout=600):
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RelayError("命令失败：%s\n%s" % (" ".join(cmd[:4]), (p.stderr or p.stdout)[-400:]))
    return p.stdout.strip()


def _file_sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def snap_dir(cfg, code):
    return common.snapshots_dir(cfg) / code


def check_space(cfg, code, proj, man=None):
    """目标盘剩余空间不足预计大小的 2 倍就不开始。返回 (预计字节, 剩余字节)。"""
    man = man or manifest.take(proj["path"], light=True)
    need = manifest.total_size(man, proj.get("raw_dirs", ()))
    target = snap_dir(cfg, code)
    target.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(str(target)).free
    if free < need * 2:
        raise RelayError("放快照的盘空间不够：预计要 %.1f MB，只剩 %.1f MB（要求剩余至少 2 倍）。"
                         % (need / 1e6, free / 1e6))
    return need, free


def take(cfg, code, label=""):
    """拍一份快照。返回记录 dict（也存成 <时间>.json）。"""
    proj = common.project(cfg, code)
    path = common.real(proj["path"])
    check_space(cfg, code, proj)
    ts = common.stamp()
    out = snap_dir(cfg, code)
    n = 0
    while (out / ("%s.json" % ts)).exists():
        n += 1
        ts = "%s-%d" % (common.stamp(), n)
    rec = {"id": ts, "code": code, "label": label, "taken": common.now_iso(), "project": path}
    if manifest.git_dir(path):
        rec.update(_take_git(path, out, ts))
    else:
        rec.update(_take_rsync(path, out, ts, proj.get("raw_dirs", ())))
    common.atomic_write_json(out / ("%s.json" % ts), rec)
    audit.log(code, "relay", "快照", "%s %s %s" % (ts, rec["kind"], label))
    return rec


def _take_git(path, out, ts):
    fd, idx = tempfile.mkstemp(prefix="relay-index-")
    os.close(fd)
    os.unlink(idx)
    env = dict(os.environ, GIT_INDEX_FILE=idx,
               GIT_AUTHOR_NAME="relay", GIT_AUTHOR_EMAIL="relay@localhost",
               GIT_COMMITTER_NAME="relay", GIT_COMMITTER_EMAIL="relay@localhost")
    try:
        head = subprocess.run(["git", "-C", path, "rev-parse", "-q", "--verify", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        if head:
            _run(["git", "-C", path, "read-tree", head], env=env)
        _run(["git", "-C", path, "add", "-A", "."], env=env)
        tree = _run(["git", "-C", path, "write-tree"], env=env)
        cmd = ["git", "-C", path, "commit-tree", tree, "-m", "relay snapshot %s" % ts]
        if head:
            cmd += ["-p", head]
        commit = _run(cmd, env=env)
    finally:
        try:
            os.unlink(idx)
        except OSError:
            pass
    ref = "refs/relay-snap/%s" % ts
    _run(["git", "-C", path, "update-ref", ref, commit])
    tar = out / ("%s.tar.gz" % ts)
    _run(["git", "-C", path, "archive", "--format=tar.gz", "-o", str(tar), commit])
    return {"kind": "git", "ref": ref, "commit": commit, "tree": tree, "head": head or None,
            "archive": str(tar), "archive_sha256": _file_sha(str(tar))}


def _take_rsync(path, out, ts, raw_dirs):
    dest = out / ts
    prev = None
    for name in sorted(os.listdir(str(out)), reverse=True):
        if (out / name).is_dir() and not name.startswith("restore-"):
            prev = out / name
            break
    cmd = ["rsync", "-a"]
    for name in sorted(manifest.EXCLUDE_NAMES):
        cmd += ["--exclude", "/%s" % name]
    for rel in sorted(manifest.EXCLUDE_RELS):
        cmd += ["--exclude", "/%s" % rel]
    for d in raw_dirs:
        cmd += ["--exclude", "/%s" % d.strip("/")]
    if prev:
        cmd += ["--link-dest", str(prev)]
    cmd += [path.rstrip("/") + "/", str(dest) + "/"]
    _run(cmd, timeout=3600)
    return {"kind": "rsync", "dir": str(dest), "link_dest": str(prev) if prev else None}


def list_snaps(cfg, code):
    out = snap_dir(cfg, code)
    recs = []
    if out.is_dir():
        for name in sorted(os.listdir(str(out))):
            if name.endswith(".json"):
                r = common.read_json(out / name)
                if r:
                    recs.append(r)
    return recs


def verify(rec):
    """核对快照没被动过。返回问题清单（空＝没问题）。"""
    problems = []
    if rec["kind"] == "git":
        if not os.path.exists(rec["archive"]):
            problems.append("压缩包不见了")
        elif _file_sha(rec["archive"]) != rec["archive_sha256"]:
            problems.append("压缩包指纹对不上")
        p = subprocess.run(["git", "-C", rec["project"], "rev-parse", "-q", "--verify",
                            rec["ref"] + "^{tree}"], capture_output=True, text=True)
        if p.stdout.strip() != rec["tree"]:
            problems.append("工程里的快照引用被改了或不见了（以压缩包为准）")
    elif not os.path.isdir(rec["dir"]):
        problems.append("快照文件夹不见了")
    return problems


def restore(cfg, code, snap_id=None):
    """把快照解到一个新文件夹（不覆盖原处），并列出它和当前工程的差异。"""
    recs = list_snaps(cfg, code)
    if not recs:
        raise RelayError("这个工程还没有快照。")
    rec = recs[-1] if not snap_id else next((r for r in recs if r["id"] == snap_id), None)
    if rec is None:
        raise RelayError("找不到快照 %s" % snap_id)
    dest = snap_dir(cfg, code) / ("restore-%s-at-%s" % (rec["id"], common.stamp()))
    dest.mkdir(parents=True)
    if rec["kind"] == "git":
        if _file_sha(rec["archive"]) != rec["archive_sha256"]:
            raise RelayError("快照压缩包的指纹对不上，不能用它恢复。")
        _run(["tar", "-xzf", rec["archive"], "-C", str(dest)])
    else:
        _run(["rsync", "-a", rec["dir"].rstrip("/") + "/", str(dest) + "/"], timeout=3600)
    old = manifest.take(str(dest))
    cur = manifest.take(rec["project"])
    covered = set(old["files"])
    d = manifest.diff({"files": old["files"]}, {"files": cur["files"]})
    # 快照没收进来的文件（被忽略的、素材）不算“新增”
    if rec["kind"] == "git":
        ignored = _git_ignored(rec["project"], d["added"])
        d["added"] = [p for p in d["added"] if p not in ignored]
    proj = common.project(cfg, code)
    raw = tuple(x.strip("/") + "/" for x in proj.get("raw_dirs", ()))
    d["added"] = [p for p in d["added"] if not (raw and (p + "/").startswith(raw))]
    audit.log(code, "relay", "恢复到新文件夹", "%s → %s" % (rec["id"], dest))
    return {"snapshot": rec, "dest": str(dest), "files": len(covered),
            "now_vs_snapshot": {"现在多出来的": d["added"], "现在少了的": d["removed"], "内容不同的": d["changed"]}}


def _git_ignored(path, rels):
    if not rels:
        return set()
    p = subprocess.run(["git", "-C", path, "check-ignore", "--stdin"], input="\n".join(rels),
                       capture_output=True, text=True)
    return set(p.stdout.splitlines())


def usage(cfg):
    """快照占用（字节），按工程。"""
    res = {}
    base = common.snapshots_dir(cfg)
    if base.is_dir():
        for code in sorted(os.listdir(str(base))):
            p = subprocess.run(["du", "-sk", str(base / code)], capture_output=True, text=True)
            try:
                res[code] = int(p.stdout.split()[0]) * 1024
            except Exception:
                res[code] = 0
    return res


# ---------- 素材只读 ----------

def _perm_file(code):
    return common.ensure_private_dir(common.STATE / "perms") / ("%s.json" % code)


def protect(cfg, code):
    """素材目录设只读（chmod a-w + chflags uchg）。先把原权限存成清单。重复执行无害。"""
    proj = common.project(cfg, code)
    saved = common.read_json(_perm_file(code), {}) or {}
    done = []
    for d in proj.get("raw_dirs", ()):
        root = os.path.join(common.real(proj["path"]), d.strip("/"))
        if not os.path.isdir(root):
            continue
        if d not in saved:
            perms = {}
            for dirpath, dirnames, filenames in os.walk(root):
                for name in [""] + dirnames + filenames:
                    full = os.path.join(dirpath, name) if name else dirpath
                    if not os.path.islink(full):
                        perms[os.path.relpath(full, root)] = stat.S_IMODE(os.lstat(full).st_mode)
            saved[d] = perms
            common.atomic_write_json(_perm_file(code), saved)
        _run(["chmod", "-R", "a-w", root])
        _run(["chflags", "-R", "uchg", root])
        done.append(d)
    if done:
        audit.log(code, "relay", "素材只读", ", ".join(done))
    return done


def unprotect(cfg, code):
    """撤回素材只读：去掉 uchg，按清单还原权限。"""
    proj = common.project(cfg, code)
    saved = common.read_json(_perm_file(code), {}) or {}
    done = []
    for d in proj.get("raw_dirs", ()):
        root = os.path.join(common.real(proj["path"]), d.strip("/"))
        if not os.path.isdir(root):
            continue
        _run(["chflags", "-R", "nouchg", root])
        for rel, mode in (saved.get(d) or {}).items():
            full = root if rel == "." else os.path.join(root, rel)
            if os.path.lexists(full):
                os.chmod(full, mode)
        done.append(d)
    if done:
        saved = {k: v for k, v in saved.items() if k not in done}
        common.atomic_write_json(_perm_file(code), saved)
        audit.log(code, "relay", "撤回素材只读", ", ".join(done))
    return done


def is_protected(cfg, code):
    saved = common.read_json(_perm_file(code), {}) or {}
    proj = common.project(cfg, code)
    return all(d in saved for d in proj.get("raw_dirs", ())) if proj.get("raw_dirs") else True
