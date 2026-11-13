# -*- coding: utf-8 -*-
"""工程清单与对比：谁改了哪些文件，以这里的对比为准。

完整清单：路径、大小、修改时间、（小于 20MB 的）内容指纹；另含钩子配置区和 Git 指向。
轻量清单：只记路径、大小、修改时间（Stop 钩子里用，要快）。
"""
import hashlib
import os
import subprocess

from . import common

EXCLUDE_NAMES = {".git", ".venv", "venv", "node_modules", "__pycache__", ".tri-agent-relay", ".DS_Store"}
EXCLUDE_RELS = {"docs/relay"}
HASH_LIMIT = 20 * 1024 * 1024


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _entry(full, light):
    st = os.lstat(full)
    if os.path.islink(full):
        return {"s": st.st_size, "m": st.st_mtime_ns, "l": os.readlink(full)}
    e = {"s": st.st_size, "m": st.st_mtime_ns}
    if not light and st.st_size <= HASH_LIMIT:
        try:
            e["h"] = _sha(full)
        except OSError:
            e["h"] = "unreadable"
    return e


def _walk(root, light, base=""):
    files = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir = "" if rel_dir == "." else rel_dir
        keep = []
        for d in dirnames:
            rel = os.path.join(rel_dir, d) if rel_dir else d
            full = os.path.join(dirpath, d)
            if os.path.islink(full):
                files[os.path.join(base, rel)] = _entry(full, light)
                continue
            if not base and (d in EXCLUDE_NAMES or rel in EXCLUDE_RELS):
                continue
            keep.append(d)
        dirnames[:] = keep
        for name in filenames:
            if not base and name in EXCLUDE_NAMES:
                continue
            rel = os.path.join(rel_dir, name) if rel_dir else name
            try:
                files[os.path.join(base, rel)] = _entry(os.path.join(dirpath, name), light)
            except OSError:
                continue
    return files


def git_dir(project_path):
    """工程的 Git 数据目录（可能在工程外）；不是 Git 工程返回 None。"""
    try:
        out = subprocess.run(["git", "-C", str(project_path), "rev-parse", "--absolute-git-dir"],
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return None


def _git_refs(project_path):
    refs = {}
    try:
        head = subprocess.run(["git", "-C", str(project_path), "rev-parse", "-q", "--verify", "HEAD"],
                              capture_output=True, text=True, timeout=10)
        refs["HEAD"] = head.stdout.strip() or "(无提交)"
        sym = subprocess.run(["git", "-C", str(project_path), "symbolic-ref", "-q", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        refs["HEAD@"] = sym.stdout.strip() or "(游离)"
        out = subprocess.run(["git", "-C", str(project_path), "for-each-ref",
                              "--format=%(refname) %(objectname)", "refs/heads", "refs/tags"],
                             capture_output=True, text=True, timeout=10)
        for line in out.stdout.splitlines():
            name, _, sha = line.partition(" ")
            refs[name] = sha
    except Exception:
        pass
    return refs


def protected_files(project_path):
    """钩子配置区：.git/hooks 和 .git/config（.claude/ 在普通清单里，按前缀归类）。永远算指纹。"""
    out = {}
    gd = git_dir(project_path)
    if gd:
        cfg = os.path.join(gd, "config")
        if os.path.exists(cfg):
            out["<git>/config"] = _entry(cfg, False)
        hooks = os.path.join(gd, "hooks")
        if os.path.isdir(hooks):
            out.update(_walk(hooks, False, base="<git>/hooks"))
    return out


def take(project_path, light=False):
    project_path = common.real(project_path)
    man = {
        "taken": common.now_iso(),
        "path": project_path,
        "light": bool(light),
        "files": _walk(project_path, light),
    }
    if not light:
        man["protected"] = protected_files(project_path)
        man["git"] = _git_refs(project_path) if git_dir(project_path) else None
    return man


def _same(a, b):
    if a.get("l") != b.get("l") or a["s"] != b["s"]:
        return False
    if "h" in a and "h" in b:
        return a["h"] == b["h"]
    return a["m"] == b["m"]


def _diff_map(old, new):
    added = sorted(k for k in new if k not in old)
    removed = sorted(k for k in old if k not in new)
    changed = sorted(k for k in new if k in old and not _same(old[k], new[k]))
    return added, removed, changed


def diff(old, new, raw_dirs=()):
    """对比两份清单。返回 dict：added/removed/changed（普通文件）、raw（素材区变化）、
    protected（钩子配置区变化，含 .claude/）、git（HEAD/分支变化）。"""
    added, removed, changed = _diff_map(old.get("files", {}), new.get("files", {}))
    raw_prefixes = tuple(d.strip("/") + "/" for d in raw_dirs)

    def is_raw(p):
        return bool(raw_prefixes) and (p + "/").startswith(raw_prefixes)

    def is_prot(p):
        return p == ".claude" or p.startswith(".claude/")

    res = {"added": [], "removed": [], "changed": [], "raw": [], "protected": [], "git": []}
    for kind, items in (("added", added), ("removed", removed), ("changed", changed)):
        for p in items:
            if is_raw(p):
                res["raw"].append("%s（%s）" % (p, {"added": "新增", "removed": "删除", "changed": "改动"}[kind]))
            elif is_prot(p):
                res["protected"].append("%s（%s）" % (p, {"added": "新增", "removed": "删除", "changed": "改动"}[kind]))
            else:
                res[kind].append(p)
    if "protected" in old and "protected" in new:
        a, r, c = _diff_map(old["protected"], new["protected"])
        res["protected"] += ["%s（新增）" % p for p in a] + ["%s（删除）" % p for p in r] + ["%s（改动）" % p for p in c]
    go, gn = old.get("git"), new.get("git")
    if go is not None and gn is not None:
        for k in sorted(set(go) | set(gn)):
            if go.get(k) != gn.get(k):
                res["git"].append("%s：%s → %s" % (k, (go.get(k) or "无")[:12], (gn.get(k) or "无")[:12]))
    return res


def is_empty(d, ignore_git=False):
    keys = ["added", "removed", "changed", "raw", "protected"] + ([] if ignore_git else ["git"])
    return not any(d[k] for k in keys)


def summarize(d, limit=40):
    lines = []
    for key, title in (("added", "新增"), ("changed", "改动"), ("removed", "删除"),
                       ("raw", "素材区变化（不应该有）"), ("protected", "钩子配置区变化（不应该有）"),
                       ("git", "Git 指向变化")):
        items = d.get(key) or []
        if items:
            lines.append("%s %d 项：" % (title, len(items)))
            lines += ["  - %s" % p for p in items[:limit]]
            if len(items) > limit:
                lines.append("  - …另有 %d 项" % (len(items) - limit))
    return "\n".join(lines) if lines else "没有变化"


def total_size(man, raw_dirs=()):
    raw_prefixes = tuple(d.strip("/") + "/" for d in raw_dirs)
    return sum(e["s"] for p, e in man["files"].items()
               if not (raw_prefixes and (p + "/").startswith(raw_prefixes)))


# ---------- 存取（都在程序文件夹 state/manifests/<代号>/ 下，工程外） ----------

def store_dir(code):
    return common.ensure_private_dir(common.STATE / "manifests" / code)


def save(code, name, man):
    common.atomic_write_json(store_dir(code) / ("%s.json" % name), man)


def load(code, name):
    return common.read_json(store_dir(code) / ("%s.json" % name))


def fingerprint_protected(man):
    """钩子配置区（.claude/ + .git/hooks + .git/config）的总指纹。"""
    h = hashlib.sha256()
    items = dict(man.get("protected") or {})
    for p, e in man.get("files", {}).items():
        if p == ".claude" or p.startswith(".claude/"):
            items[p] = e
    for p in sorted(items):
        e = items[p]
        h.update(("%s\0%s\0%s\0%s\n" % (p, e.get("s"), e.get("h"), e.get("l"))).encode("utf-8"))
    return h.hexdigest()
