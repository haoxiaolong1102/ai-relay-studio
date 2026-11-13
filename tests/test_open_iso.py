# -*- coding: utf-8 -*-
"""轻量“开工/收工”（Antigravity）和隔离分支提交/推送的自测。不调任何模型。"""
import os
import unittest

from rtest import Env, sh


def git(e, *args, cwd=None):
    return sh(["git"] + list(args), cwd=cwd or e.proj).stdout.strip()


class OpenBase(unittest.TestCase):
    git = True

    def setUp(self):
        self.e = Env(git=self.git)
        self.addCleanup(self.e.cleanup)

    def open(self, tool="antigravity", **kw):
        return self.e.relay("open", "--project", "X", "--tool", tool, answers=[], **kw)

    def close(self, answers=None):
        return self.e.relay("takeback", "--project", "X", answers=answers or [])

    def edit(self):
        with open(os.path.join(self.e.proj, "src", "main.py"), "a") as f:
            f.write("print('by antigravity')\n")
        with open(os.path.join(self.e.proj, "new.txt"), "w") as f:
            f.write("new\n")


class OpenCloseTests(OpenBase):
    def test_open_then_close(self):
        e = self.e
        r = self.open()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(e.lock_holder(), "antigravity")
        self.assertEqual(e.holders_holder(), "antigravity")
        self.assertIn("Antigravity", r.stdout)
        # Claude 这边被拦（垫片看抄本，钩子看锁）
        st = e.relay("status")
        self.assertIn("antigravity", st.stdout)
        # 再开一次被拒；交给 Codex 也被拒
        r2 = self.open()
        self.assertNotEqual(r2.returncode, 0)
        self.assertEqual(e.lock_holder(), "antigravity")
        r3 = e.relay("handoff", "--project", "X", answers=[])
        self.assertNotEqual(r3.returncode, 0)
        self.edit()
        r4 = self.close()
        self.assertEqual(r4.returncode, 0, r4.stdout + r4.stderr)
        self.assertEqual(e.lock_holder(), "claude")
        self.assertEqual(e.holders_holder(), "claude")
        b = e.read("docs/relay/BATON.md")
        self.assertIn("接手的工具：Antigravity", b)
        self.assertIn("src/main.py", b[b.index("接力期间"):])
        self.assertIn("new.txt", b[b.index("接力期间"):])
        self.assertIn("锁与内置盘抄本：一致", e.relay("status").stdout)

    def test_open_refused_when_switch_off(self):
        self.e.set_cfg(live_enabled=False)
        r = self.open()
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.e.lock_holder(), "claude")

    def test_stale_codex_manifest_does_not_warn(self):
        """先跑一轮假 Codex 留下 codex-last 清单，再开工/收工：不该冒出“Codex 最后一段之后有人改了文件”的警告。"""
        e = self.e
        self.assertEqual(e.relay("handoff", "--project", "X", answers=[], FAKE_CODEX_SCENARIO="ok").returncode, 0)
        self.assertEqual(e.relay("takeback", "--project", "X", answers=[]).returncode, 0)
        self.assertEqual(self.open().returncode, 0)
        self.edit()
        r = self.close()          # 没给任何弹窗答案：要是出了警告框，这里就会失败
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(e.lock_holder(), "claude")


class OpenCloseNoGit(OpenCloseTests):
    git = False


class IsolatedBranchTests(OpenBase):
    def setUp(self):
        super().setUp()
        self.remote = os.path.join(self.e.tmp, "remote.git")
        sh(["git", "init", "-q", "--bare", self.remote])
        git(self.e, "remote", "add", "origin", self.remote)
        git(self.e, "push", "-q", "origin", "main")
        self.e.set_cfg(git={"isolated_commit": True, "push": True, "remote": "origin", "max_file_mb": 1})

    def branches(self, cwd=None):
        out = git(self.e, "for-each-ref", "--format=%(refname)", "refs/heads/relay", cwd=cwd)
        return [x for x in out.splitlines() if x]

    def test_isolated_commit_and_push(self):
        e = self.e
        head0 = git(e, "rev-parse", "HEAD")
        self.assertEqual(self.open().returncode, 0)
        self.edit()
        with open(os.path.join(e.proj, "big.bin"), "wb") as f:      # 超过 1 MB 的新文件：不该收进分支
            f.write(b"\0" * (2 * 1024 * 1024))
        r = self.close()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        br = self.branches()
        self.assertEqual(len(br), 1, br)
        self.assertTrue(br[0].startswith("refs/heads/relay/antigravity/"))
        # 隔离：当前分支、HEAD、工作区、索引都没动
        self.assertEqual(git(e, "rev-parse", "HEAD"), head0)
        self.assertEqual(git(e, "symbolic-ref", "HEAD"), "refs/heads/main")
        status = git(e, "status", "--porcelain")
        self.assertIn("M src/main.py", status)
        self.assertNotIn("M  src/main.py", status)      # 没被放进暂存区
        self.assertIn("?? new.txt", status)
        # 溯源：父提交是开工时的 HEAD，作者写明 Antigravity
        self.assertEqual(git(e, "rev-parse", br[0] + "^"), head0)
        self.assertIn("Antigravity", git(e, "log", "-1", "--format=%an", br[0]))
        self.assertIn(head0, git(e, "log", "-1", "--format=%B", br[0]))
        files = git(e, "diff", "--name-only", head0, br[0]).splitlines()
        self.assertIn("src/main.py", files)
        self.assertIn("new.txt", files)
        self.assertNotIn("big.bin", files)
        self.assertFalse([f for f in files if f.startswith("docs/relay/")])
        # 推送：远程只多了这一个分支，main 没变
        self.assertEqual(self.branches(cwd=self.remote), br)
        self.assertEqual(git(e, "rev-parse", "refs/heads/main", cwd=self.remote), head0)
        self.assertIn("已推到", e.read("docs/relay/BATON.md"))
        # 再来一轮：不会因为上一轮留下的分支报“Git 指向变了”
        self.assertEqual(self.open().returncode, 0)
        r2 = self.close()
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        self.assertEqual(len(self.branches()), 1)      # 没有新改动 → 不建新分支

    def test_preexisting_changes_kept_separate(self):
        """开工前工作区里已有的未提交改动：单独一个“开工前”提交，不算在 Antigravity 头上。"""
        e = self.e
        head0 = git(e, "rev-parse", "HEAD")
        with open(os.path.join(e.proj, "a.txt"), "a") as f:
            f.write("claude 没提交的改动\n")
        self.assertEqual(self.open().returncode, 0)
        self.edit()
        r = self.close()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        br = self.branches()
        self.assertEqual(len(br), 1, br)
        pre = git(e, "rev-parse", br[0] + "^")
        self.assertNotEqual(pre, head0)
        self.assertEqual(git(e, "rev-parse", pre + "^"), head0)
        self.assertEqual(git(e, "diff", "--name-only", head0, pre).splitlines(), ["a.txt"])
        tool_files = git(e, "diff", "--name-only", pre, br[0]).splitlines()
        self.assertIn("src/main.py", tool_files)
        self.assertNotIn("a.txt", tool_files)
        self.assertNotIn("Antigravity", git(e, "log", "-1", "--format=%an", pre))
        self.assertEqual(git(e, "rev-parse", "HEAD"), head0)

    def test_raw_dir_already_gitignored(self):
        """素材目录本来就写在 .gitignore 里（真实测试夹就是这样）：照样能建分支。"""
        e = self.e
        with open(os.path.join(e.proj, ".gitignore"), "a") as f:
            f.write("raw/\n")
        git(e, "rm", "-r", "-q", "--cached", "raw")
        git(e, "add", ".gitignore")
        git(e, "commit", "-q", "-m", "ignore raw")
        git(e, "push", "-q", "origin", "main")
        self.assertEqual(self.open().returncode, 0)
        self.edit()
        r = self.close()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(self.branches()), 1, e.read("docs/relay/BATON.md")[-600:])

    def test_codex_run_also_gets_branch(self):
        e = self.e
        self.assertEqual(e.relay("handoff", "--project", "X", answers=[], FAKE_CODEX_SCENARIO="ok").returncode, 0)
        r = e.relay("takeback", "--project", "X", answers=[])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        br = self.branches()
        self.assertEqual(len(br), 1, br)
        self.assertTrue(br[0].startswith("refs/heads/relay/codex/"))
        self.assertEqual(self.branches(cwd=self.remote), br)

    def test_push_failure_does_not_block(self):
        e = self.e
        git(e, "remote", "set-url", "origin", os.path.join(e.tmp, "nowhere.git"))
        self.assertEqual(self.open().returncode, 0)
        self.edit()
        r = self.close()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(e.lock_holder(), "claude")
        self.assertEqual(len(self.branches()), 1)
        self.assertIn("推送没成功", e.read("docs/relay/BATON.md"))

    def test_off_by_default(self):
        e = self.e
        e.cfg.pop("git")
        e.save_cfg()
        self.assertEqual(self.open().returncode, 0)
        self.edit()
        self.assertEqual(self.close().returncode, 0)
        self.assertEqual(self.branches(), [])


if __name__ == "__main__":
    unittest.main()
