# -*- coding: utf-8 -*-
"""全流程（假 Codex）、测试命令沙箱。"""
import glob
import json
import os
import shutil
import socket
import tempfile
import unittest

import rtest
from rtest import Env, HERE, PY, real

from relaylib.redact import UNTRUSTED_HEAD, UNTRUSTED_TAIL  # noqa: E402

FAKE_KEY = "sk-FAKEFAKEFAKEFAKEFAKE1234"


class FlowBase(unittest.TestCase):
    git = True
    env_kw = {}

    def setUp(self):
        self.e = Env(git=self.git, **self.env_kw)
        self.addCleanup(self.e.cleanup)

    def handoff(self, scenario, answers=None, extra=(), **kw):
        return self.e.relay("handoff", "--project", "X", *extra, answers=answers if answers is not None else [],
                            FAKE_CODEX_SCENARIO=scenario, **kw)

    def takeback(self, answers=None, **kw):
        return self.e.relay("takeback", "--project", "X", answers=answers if answers is not None else [], **kw)

    def baton(self):
        return self.e.read("docs/relay/BATON.md")

    def runs(self, pattern="*"):
        return glob.glob(os.path.join(self.e.state, "runs", "X", pattern))

    def outcome(self):
        return self.e.summary()["outcome"]


class FlowTests(FlowBase):
    def test_handoff_then_takeback(self):
        """handoff(ok)：锁变 codex、有快照、有 runs/*.jsonl、BATON 头部持棒者 codex；再 handoff 被拒；
        takeback：锁回 claude、抄本一致、BATON 有“接力期间”并列出被改文件、HANDBACK 被不可信标记包住。"""
        e = self.e
        r = self.handoff("ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(e.lock_holder(), "codex")
        self.assertEqual(e.holders_holder(), "codex")
        self.assertTrue(glob.glob(os.path.join(e.snaps, "X", "*.json")))
        self.assertTrue(self.runs("*.jsonl"))
        self.assertIn("持棒者：codex", self.baton().split("\n\n")[1] if "\n\n" in self.baton() else self.baton())
        self.assertIn("Codex 报告：完成", self.outcome())
        # 再交一次被拒
        r2 = self.handoff("ok")
        self.assertNotEqual(r2.returncode, 0)
        self.assertIn("已经在 codex", r2.stdout)
        self.assertEqual(e.lock_holder(), "codex")
        # 交回
        r3 = self.takeback()
        self.assertEqual(r3.returncode, 0, r3.stdout + r3.stderr)
        self.assertEqual(e.lock_holder(), "claude")
        self.assertEqual(e.holders_holder(), "claude")
        st = e.relay("status")
        self.assertIn("锁与内置盘抄本：一致", st.stdout)
        b = self.baton()
        self.assertIn("持棒者：claude", b)
        self.assertIn("接力期间", b)
        self.assertIn("src/main.py", b[b.index("接力期间"):])
        self.assertIn("交回前快照", b)
        i, j = b.index(UNTRUSTED_HEAD), b.index(UNTRUSTED_TAIL)
        self.assertIn("状态：完成", b[i:j])

    def test_from_chat_cancel(self):
        """--from-chat 且弹窗答“取消” → 不交出，锁仍是 claude，审计有记录。"""
        r = self.handoff("ok", answers=["取消"], extra=("--from-chat",))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.e.lock_holder(), "claude")
        self.assertIn("已取消", r.stdout)
        self.assertIn("交出取消", self.e.audit_text())
        self.assertFalse(self.runs("*.jsonl"))

    def test_from_chat_confirm(self):
        """--from-chat 答“交给 Codex” → 正常交出。"""
        r = self.handoff("ok", answers=["交给 Codex"], extra=("--from-chat",))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.e.lock_holder(), "codex")

    def test_partial_runs_to_max_segments(self):
        """partial（每段都改文件、HANDBACK 写未完成）→ 跑满 max_segments 段。"""
        r = self.handoff("partial")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(self.e.summary()["segments"]), 3)
        self.assertIn("已跑满 3 段", self.outcome())

    def test_hang_segment_timeout(self):
        """hang + 很小的单段时限 → 该段超时中止，outcome 提到单段时限。"""
        self.e.cfg["codex"]["segment_timeout_min"] = 0.05
        self.e.save_cfg()
        r = self.handoff("hang", timeout=90)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("单段时限", self.outcome())
        self.assertEqual(len(self.e.summary()["segments"]), 1)

    def test_quota(self):
        """quota（错误事件含 usage limit 且非零退出）→ outcome 提到额度。"""
        r = self.handoff("quota")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("额度", self.outcome())

    def test_leak_not_in_runs(self):
        """leak：state/runs/ 下所有文件里搜不到假密钥原文，也没有 aggregated_output 正文。"""
        r = self.handoff("leak")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        files = [p for p in glob.glob(os.path.join(self.e.state, "runs", "**", "*"), recursive=True) if os.path.isfile(p)]
        self.assertTrue(files)
        for p in files:
            with open(p, encoding="utf-8", errors="replace") as f:
                t = f.read()
            for needle in (FAKE_KEY, "supersecretvalue123", "AGGREGATED-BODY-MARKER"):
                self.assertNotIn(needle, t, "%s 里有 %s" % (p, needle))

    def test_leak_last_message_not_in_runs(self):
        """leak 且 Codex 最后一句话也带密钥：runs/ 下（含 .last.txt）也不该有原文。"""
        r = self.handoff("leak", FAKE_CODEX_LEAK_LAST="1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for p in glob.glob(os.path.join(self.e.state, "runs", "X", "*")):
            with open(p, encoding="utf-8", errors="replace") as f:
                t = f.read()
            for needle in (FAKE_KEY, "supersecretvalue123"):
                self.assertNotIn(needle, t, "%s 里有 %s" % (p, needle))

    def test_queue_media(self):
        """queue_media：工程外和符号链接的请求被拒（.result.json 状态“拒绝”+审计“拒绝”行）；合法请求弹窗答“不执行”则不执行。"""
        e = self.e
        out = os.path.join(e.outside, "x.wav")
        with open(out, "wb") as f:
            f.write(b"RIFF0000WAVE")
        r = self.handoff("queue_media", answers=["不执行"], FAKE_CODEX_OUTSIDE=out)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        qd = os.path.join(e.proj, "docs", "relay", "media_requests")
        res = {}
        for n in ("1-outside", "2-symlink", "3-legal"):
            with open(os.path.join(qd, n + ".result.json"), encoding="utf-8") as f:
                res[n] = json.load(f)
            self.assertEqual(res[n]["status"], "拒绝", n)
        self.assertIn("不在工程文件夹内", res["1-outside"]["reason"])
        self.assertIn("不在工程文件夹内", res["2-symlink"]["reason"])
        self.assertIn("不执行", res["3-legal"]["reason"])
        self.assertEqual(e.remaining_answers(), [])
        a = e.audit_text()
        self.assertGreaterEqual(a.count("| 拒绝 |"), 3)
        self.assertFalse(os.path.exists(os.path.join(e.proj, "analysis")))

    def test_edit_after_codex_warns_on_takeback(self):
        """Codex 最后一段结束与 takeback 之间有人改了文件 → takeback 出警告；答“先不交回”则锁仍是 codex。"""
        self.assertEqual(self.handoff("ok").returncode, 0)
        with open(os.path.join(self.e.proj, "a.txt"), "a") as f:
            f.write("someone else edited me\n")
        r = self.takeback(answers=["先不交回"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("最后一段结束之后", r.stdout)
        self.assertIn("a.txt", r.stdout)
        self.assertEqual(self.e.lock_holder(), "codex")
        self.assertEqual(self.e.holders_holder(), "codex")

    def test_takeback_when_claude_holds_refused(self):
        """棒本来在 claude 手里时 takeback 被拒。"""
        r = self.takeback()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("本来就在 Claude", r.stdout)


class FlowNoGit(FlowTests):
    git = False


class NoProgressTests(FlowBase):
    env_kw = {"test_cmd": None}

    def test_noop_stops_after_two_idle_segments(self):
        """noop 连续两段（无测试命令）→ 以“没有任何进展”停，共 2 段。"""
        r = self.handoff("noop")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("没有任何进展", self.outcome())
        self.assertEqual(len(self.e.summary()["segments"]), 2)


class NoProgressWithTestCmdTests(FlowBase):
    def test_noop_with_test_cmd(self):
        """配置了测试命令时，noop 也应在“连续两段无进展”后停（第一段测试结果从 None 变 pass 不应算进展）。"""
        r = self.handoff("noop")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("没有任何进展", self.outcome())
        self.assertEqual(len(self.e.summary()["segments"]), 2)


class ViolationMixin(object):
    scenario = None
    stop_word = None
    warn_word = None

    def _run(self):
        r = self.handoff(self.scenario)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(self.e.summary()["segments"]), 1, "该段后应停下")
        self.assertIn(self.stop_word, self.outcome())
        self.assertEqual(self.e.lock_holder(), "codex")

    def test_stop_and_takeback_warning(self):
        """违规发生后：该段后停、outcome 提到问题；takeback 弹警告，答“先不交回”锁仍是 codex，答“仍然交回”才交回。"""
        self._run()
        r = self.takeback(answers=["先不交回"])
        self.assertIn("交回前发现下面的情况", r.stdout, "takeback 没弹警告：\n" + r.stdout)
        self.assertEqual(self.e.lock_holder(), "codex")
        self.assertEqual(self.e.holders_holder(), "codex")
        self.assertIn(self.warn_word, r.stdout)
        r = self.takeback(answers=["我知道了，仍然交回"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.e.lock_holder(), "claude")
        self.assertIn("带着警告交回", self.e.audit_text())


class NewRawTests(ViolationMixin, FlowBase):
    scenario, stop_word, warn_word = "new_raw", "素材区被动了", "素材区有变化"


class TouchHooksTests(ViolationMixin, FlowBase):
    scenario, stop_word, warn_word = "touch_hooks", "钩子配置区被动了", "钩子配置"


class GitCommitTests(ViolationMixin, FlowBase):
    scenario, stop_word, warn_word = "git_commit", "Git 的提交", "Git"


class RawProtectionHoldsTests(FlowBase):
    def test_raw_write_denied_when_protected(self):
        """素材已只读且 Codex 不越狱：改 raw 失败，raw 内容不变，没有“素材区被动了”。"""
        before = self.e.tree(root=os.path.join(self.e.proj, "raw"), skip=())
        r = self.handoff("touch_raw", FAKE_CODEX_NO_ESCAPE="1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.e.tree(root=os.path.join(self.e.proj, "raw"), skip=()), before)
        self.assertNotIn("素材区被动了", self.outcome())
        self.assertGreaterEqual(self.e.summary()["segments"][0]["denials"], 1)


class SandboxTests(unittest.TestCase):
    SCRIPT = '''
import os, socket, sys
out = os.environ["PROBE_OUTSIDE"]; port = int(os.environ["PROBE_PORT"])
open("ran.txt", "w").write("ran")
res = []
try:
    open(os.path.join(out, "leak.txt"), "w").write("x"); res.append("wrote")
except Exception:
    res.append("write-blocked")
try:
    socket.create_connection(("127.0.0.1", port), timeout=2).close(); res.append("net-open")
except Exception:
    res.append("net-blocked")
open("result.txt", "w").write(",".join(res))
'''

    def test_test_cmd_runs_in_sandbox(self):
        """测试命令在沙箱里跑：往工程外写文件失败、连本机端口失败；工程内写文件正常。"""
        outside = real(tempfile.mkdtemp(prefix="outside-", dir=HERE))   # 在临时目录之外，沙箱应拒绝写入
        self.addCleanup(shutil.rmtree, outside, True)
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(5)
        self.addCleanup(srv.close)
        e = Env(test_cmd=[PY, "sandbox_probe.py"], extra_files={"sandbox_probe.py": self.SCRIPT})
        self.addCleanup(e.cleanup)
        r = e.relay("handoff", "--project", "X", answers=[], FAKE_CODEX_SCENARIO="ok",
                    PROBE_OUTSIDE=outside, PROBE_PORT=srv.getsockname()[1])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(e.exists("result.txt"), "测试命令根本没跑起来：" + e.summary()["segments"][0]["test_tail"])
        self.assertEqual(e.read("result.txt"), "write-blocked,net-blocked")
        self.assertFalse(os.path.exists(os.path.join(outside, "leak.txt")))
        srv.settimeout(0.3)
        with self.assertRaises(socket.timeout):
            srv.accept()


if __name__ == "__main__":
    unittest.main()
