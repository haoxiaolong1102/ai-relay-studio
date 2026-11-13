# -*- coding: utf-8 -*-
"""media_ask：听/看媒体。纯音频走 agy（Antigravity 订阅），视频走 gemini-eyes（API，记账）。
Codex 持棒时只排队，由 relay 在沙箱外代办：校验路径、弹窗确认、每段最多 3 个。"""
import datetime as dt
import json
import os
import subprocess

from . import audit, common, lock, ui
from .common import RelayError
from .redact import one_line

AUDIO = {".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg", ".aiff", ".aif", ".opus"}
VIDEO = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
NOTE = "回答里的时长、音准等数字未经测量，需用代码核对。"
MAX_PER_SEGMENT = 3


def _mcfg(cfg):
    m = dict(cfg.get("media") or {})
    m.setdefault("agy_bin", "~/.local/bin/agy")
    m.setdefault("agy_models", {"fast": "gemini-3.8-flash-medium", "deep": "gemini-4-hydrogen-high"})
    m.setdefault("gemini_eyes_dir", "~/gemini-eyes")
    return m


def route_for(src, forced=None):
    if forced in ("agy", "api"):
        return forced
    ext = os.path.splitext(src)[1].lower()
    if ext in AUDIO:
        return "agy"
    if ext in VIDEO:
        return "api"
    raise RelayError("不认识的媒体类型：%s（支持常见音频、视频格式）" % ext)


def _analysis_dir(base, raw_dirs=()):
    out = os.path.join(base, "analysis")
    for d in raw_dirs:
        if common.is_inside(out, os.path.join(base, d.strip("/"))):
            raise RelayError("拒绝把结果写进素材目录")
    os.makedirs(out, exist_ok=True)
    return out


def _save(out_dir, src, record):
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    name = os.path.basename(src)
    p = os.path.join(out_dir, "%s.%s.json" % (name, stamp))
    n = 1
    while os.path.exists(p):
        p = os.path.join(out_dir, "%s.%s-%d.json" % (name, stamp, n))
        n += 1
    common.atomic_write_json(p, record, mode=0o644)
    return p


def _count(channel, cost):
    try:
        f = common.ensure_private_dir(common.STATE / "media-budget") / (dt.datetime.now().strftime("%Y%m") + ".json")
        d = common.read_json(f, {}) or {}
        d.setdefault("calls", {})
        d["calls"][channel] = d["calls"].get(channel, 0) + 1
        d["est_usd"] = round(float(d.get("est_usd", 0)) + float(cost or 0), 5)
        common.atomic_write_json(f, d)
    except Exception:
        pass


def ask(cfg, base_dir, src, question, deep=False, route=None, raw_dirs=()):
    """真正去问。base_dir＝结果放哪个工程（其下 analysis/）。返回 dict。"""
    src = common.real(src)
    if not os.path.isfile(src):
        raise RelayError("找不到文件：%s" % src)
    channel = route_for(src, route)
    out_dir = _analysis_dir(common.real(base_dir), raw_dirs)
    m = _mcfg(cfg)
    depth = "deep" if deep else "fast"
    if channel == "agy":
        model = m["agy_models"][depth]
        prompt = ("请直接听这个音频文件（不要写代码、不要用工具分析波形）：%s 。\n只依据你实际听到的内容回答。问题：%s\n"
                  "先给不超过 200 字的结论，再给完整回答，最后列出不确定之处。" % (src, question))
        try:
            p = subprocess.run([os.path.expanduser(m["agy_bin"]), "-p", prompt, "--model", model, "--mode", "plan",
                                "--print-timeout", "240s", "--output-format", "json"],
                               cwd=os.path.dirname(src), capture_output=True, text=True, timeout=300,
                               stdin=subprocess.DEVNULL)
            data = json.loads(p.stdout[p.stdout.index("{"):])
        except Exception as e:
            raise RelayError("agy 这一路没问成（%s）。可以加 --route api 改走 gemini-eyes（会花一点 API 费用）。" % type(e).__name__)
        if data.get("status") != "SUCCESS":
            raise RelayError("agy 返回失败：%s" % one_line(data.get("error") or data.get("status"), 200))
        answer = str(data.get("response") or "").strip()
        record = {"tool": "media_ask", "created": dt.datetime.now().isoformat(timespec="seconds"),
                  "channel": "agy（Antigravity 订阅）", "source": src, "question": question, "depth": depth,
                  "model": model, "modality_used": "audio", "usage": data.get("usage"), "est_cost_usd": 0,
                  "result": {"summary": answer[:200], "answer": answer, "evidence": [],
                             "caveats": "模型自述为直接听到音频，无法独立证明。"},
                  "note": NOTE}
        rf = _save(out_dir, src, record)
        _count("agy", 0)
        return {"channel": "agy", "result_file": rf, "summary": answer[:300], "cost": 0}
    # API：用 gemini-eyes 自己的虚拟环境调它现成的函数（不改它的代码；花费记进它的 ledger.csv）
    ge = os.path.expanduser(m["gemini_eyes_dir"])
    code = ("import sys, os; sys.path.insert(0, sys.argv[1]); import server; "
            "print(server._media_ask(sys.argv[2], sys.argv[3], sys.argv[4], 'auto', False))")
    env = dict(os.environ, CLAUDE_PROJECT_DIR=common.real(base_dir))
    try:
        p = subprocess.run([os.path.join(ge, ".venv", "bin", "python"), "-c", code, ge, src, question, depth],
                           cwd=common.real(base_dir), env=env, capture_output=True, text=True, timeout=900,
                           stdin=subprocess.DEVNULL)
    except Exception as e:
        raise RelayError("gemini-eyes 这一路没问成（%s）。" % type(e).__name__)
    if p.returncode != 0:
        raise RelayError("gemini-eyes 报错：%s" % one_line(p.stderr[-400:], 300))
    text = p.stdout.strip()
    rf, cost = None, 0
    for line in text.splitlines():
        if line.startswith("完整结果") and "：" in line:
            rf = line.split("：", 1)[1].strip()
    if rf and os.path.isfile(rf):
        rec = common.read_json(rf, {}) or {}
        cost = rec.get("est_cost_usd", 0)
        rec["channel"] = "gemini-eyes（API）"
        rec.setdefault("note", NOTE)
        common.atomic_write_json(rf, rec, mode=0o644)
        _count("api", cost)
    return {"channel": "api", "result_file": rf, "summary": text[:600], "cost": cost}


# ---------- 排队（Codex 持棒时） ----------

def queue_dir(project_path):
    return os.path.join(common.real(project_path), "docs", "relay", "media_requests")


def enqueue(project_path, src, question, deep=False, route=None):
    d = queue_dir(project_path)
    os.makedirs(d, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    n = 0
    while True:
        p = os.path.join(d, "%s-%d.json" % (stamp, n))
        if not os.path.exists(p):
            break
        n += 1
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"file": os.path.abspath(src), "question": question, "deep": bool(deep), "route": route,
                   "requested": dt.datetime.now().isoformat(timespec="seconds")}, f, ensure_ascii=False, indent=2)
    return p


def validate_request(req, project_path):
    """只接受工程文件夹内（解开符号链接后）的媒体文件。返回 (真实路径, None) 或 (None, 拒绝原因)。"""
    f = req.get("file") if isinstance(req, dict) else None
    if not isinstance(f, str) or not f:
        return None, "请求里没有文件路径"
    if not os.path.isabs(f):
        f = os.path.join(project_path, f)
    rp = common.real(f)
    if not common.is_inside(rp, project_path) or rp == common.real(project_path):
        return None, "文件不在工程文件夹内（或经符号链接跳到了外面）"
    if not os.path.isfile(rp):
        return None, "文件不存在"
    if os.path.splitext(rp)[1].lower() not in (AUDIO | VIDEO):
        return None, "不是媒体文件"
    if req.get("route") not in (None, "agy", "api"):
        return None, "通道参数不对"
    return rp, None


def process_queue(cfg, code, proj, project_path):
    """段后代办 Codex 排队的媒体请求。返回一句说明（没有请求返回 None）。"""
    d = queue_dir(project_path)
    if not os.path.isdir(d):
        return None
    pending = sorted(n for n in os.listdir(d) if n.endswith(".json") and not n.endswith(".result.json")
                     and not os.path.exists(os.path.join(d, n[:-5] + ".result.json")))
    if not pending:
        return None

    def finish(name, result):
        common.atomic_write_json(os.path.join(d, name[:-5] + ".result.json"), result, mode=0o644)

    accepted = []
    for i, name in enumerate(pending):
        try:
            with open(os.path.join(d, name), encoding="utf-8") as f:
                req = json.load(f)
        except Exception:
            req = None
        if i >= MAX_PER_SEGMENT:
            finish(name, {"status": "拒绝", "reason": "超过每段 %d 个的上限" % MAX_PER_SEGMENT})
            audit.log(code, "relay", "拒绝", "媒体请求超过每段上限：%s" % name)
            continue
        rp, why = validate_request(req, project_path)
        if why:
            finish(name, {"status": "拒绝", "reason": why})
            audit.log(code, "relay", "拒绝", "媒体请求 %s：%s（%s）" % (name, why, (req or {}).get("file") if isinstance(req, dict) else ""))
            continue
        accepted.append((name, rp, one_line(req.get("question"), 500), bool(req.get("deep")), req.get("route")))
    if not accepted:
        return "媒体请求 %d 个，全部被拒" % len(pending)
    listing = "\n".join("· %s\n  问：%s" % (os.path.relpath(rp, project_path), q[:120]) for _, rp, q, _, _ in accepted)
    if ui.ask("Codex 想请 Gemini 听/看下面这些文件（都在工程文件夹内）：\n\n%s\n\n要执行吗？" % listing,
              ["不执行", "执行"], "执行") != "执行":
        for name, _, _, _, _ in accepted:
            finish(name, {"status": "拒绝", "reason": "用户在弹窗里点了不执行"})
        audit.log(code, "user", "拒绝", "媒体请求 %d 个（弹窗里点了不执行）" % len(accepted))
        return "媒体请求 %d 个，用户没同意执行" % len(accepted)
    done = 0
    for name, rp, q, deep, route in accepted:
        try:
            r = ask(cfg, project_path, rp, q, deep, route, proj.get("raw_dirs", ()))
            finish(name, {"status": "完成", "result_file": r["result_file"], "channel": r["channel"]})
            audit.log(code, "relay", "媒体请求", "代办完成 %s → %s（%s）" % (os.path.basename(rp), r["result_file"], r["channel"]))
            done += 1
        except Exception as e:
            finish(name, {"status": "失败", "reason": one_line(e, 200)})
            audit.log(code, "relay", "媒体请求", "代办失败 %s：%s" % (os.path.basename(rp), e))
    return "媒体请求：代办完成 %d 个，拒绝/失败 %d 个" % (done, len(pending) - done)


def cli(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="media_ask", description="请 Gemini 听/看一个媒体文件并回答问题")
    ap.add_argument("file")
    ap.add_argument("question")
    ap.add_argument("--deep", action="store_true", help="用更强的模型")
    ap.add_argument("--route", choices=["agy", "api"], help="强制通道：agy＝订阅；api＝gemini-eyes（花钱）")
    ap.add_argument("--queue", action="store_true", help="只排队，不执行（Codex 接力期间用）")
    a = ap.parse_args(argv)
    cfg = common.load_config()
    src = os.path.abspath(a.file)
    code, proj = common.find_project(cfg, os.getcwd())
    if not code:
        code, proj = common.find_project(cfg, src)
    base = proj["path"] if proj else os.getcwd()
    codex_turn = False
    if code:
        try:
            codex_turn = lock.holder(code) != common.HOLDER_CLAUDE
        except Exception:
            codex_turn = True
    if a.queue or codex_turn:
        p = enqueue(base, src, a.question, a.deep, a.route)
        print("已排队：%s\n接力期间媒体请求统一排队：这个请求会在本段结束后由接力程序代办（需要用户点头）；结果下一段出现在 analysis/ 里，"
              "处理情况见同目录的 .result.json。每段最多 %d 个。" % (p, MAX_PER_SEGMENT))
        return 0
    route_for(src, a.route)
    r = ask(cfg, base, src, a.question, a.deep, a.route, (proj or {}).get("raw_dirs", ()))
    audit.log(code, "media_ask", "媒体请求", "%s → %s（%s）" % (os.path.basename(src), r["result_file"], r["channel"]))
    print("%s\n\n结果文件：%s\n通道：%s｜本次约 $%s\n注意：%s" % (r["summary"], r["result_file"], r["channel"], r["cost"], NOTE))
    return 0
