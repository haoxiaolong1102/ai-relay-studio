# ai-relay（AI 接力）

[English](README.md) | **简体中文**

把同一个工程像接力棒一样在 Claude Code、Codex、Antigravity 之间传：一家的额度用完了，另一家接着在同一个文件夹里干；谁改了什么查得到，随时回得去。

一个 macOS 菜单栏小图标，加一个 Python 命令（`bin/relay`）。只用标准库，没有服务器，不需要 API 密钥：它调用的是你已经登录的各家官方命令行工具。

> 现状：个人自用工具，按原样公开（v0.1）。在作者自己的 Mac 上能用；拿它做正事之前，请先看[边界](#边界)和[测过什么](#测过什么)。

## 为什么做这个

每家 AI 编程订阅都有自己的额度窗口。Claude 做到一半不回话了，通常只能干等，或者把整个工程重新讲给另一个工具听，还得祈祷两边别互相覆盖。ai-relay 把这次交接变成点一下：

- **同一时间只有一个在写。** 一把锁记着工程在谁手里。交给 Codex 或 Antigravity 期间，Claude Code 在这个工程里被钩子限制为只读。
- **不丢东西。** 每次交出前拍快照，再用逐文件清单对比，交回时能准确看到对方改了哪些文件。
- **接手的知道该干什么。** 接力棒文件（`docs/relay/BATON.md`）带着工程现状往下传，对方做完写回交接说明（`docs/relay/HANDBACK.md`）。
- **Git 保持干净。** 对方的改动记在单独的分支 `relay/<工具>/<时间>` 上，作者署名就是那个工具。你当前的分支、暂存区、工作区一概不动。

## 能做什么

| 想做什么 | 菜单里点 | 命令 |
|---|---|---|
| 让 Codex 无人值守接着做 | 交给 Codex | `relay handoff --project X` |
| 让 Antigravity 无人值守接着做 | 交给 Antigravity | `relay handoff --project X --to antigravity` |
| 自己去 Antigravity 里做（只拍快照、上锁，不启动模型） | 到 Antigravity 开工 | `relay open --project X --tool antigravity` |
| 交回 Claude | 收工 / 交回 Claude | `relay takeback --project X` |
| 看谁拿着哪个工程 | 看详细状态… | `relay status` |
| 全部关掉、修复、看日志 | 总开关 / 更多… | `relay switch` |
| 看交出之前的样子 | — | `relay restore X`（解到新文件夹，不覆盖） |

无人值守交接是分段跑的（默认最多 4 段、每段 20 分钟、总共 90 分钟）。每段之后接力程序对比文件、跑工程自己的测试，再决定要不要继续。任务做完、额度用完、动作被拦、连续两段没进展、动了不该动的地方，都会自己停下，并用白话说明原因。

## 原理

```
Claude Code ──交出──▶ 接力棒 + 快照 + 清单 ──▶ 锁：codex / antigravity
     ▲                                              │
     │                                      对方分段干活
     │                                              ▼
     └──交回── 清单对比 + 隔离分支 ◀── HANDBACK.md
```

- **锁**：`state/locks/<工程>.json`，另抄一份到 `~/.claude/relay-holders.json`，接力文件夹放在外接盘、盘没插时拦截照样有效。
- **拦截**：Claude Code 的 `PreToolUse` 钩子。棒不在 Claude 手里就拒绝写；检查本身出错也拒绝（宁可错拦）。
- **快照**：Git 工程用 git 打包，普通文件夹用 `rsync --link-dest`。
- **清单**：交出前后逐文件算指纹。以对比结果为准；对方“说”自己做了什么一律当不可信内容。
- **隔离分支**：用临时索引加 `git commit-tree` 拼出提交，再 `git push <远程> <分支>:<分支>`（从不强推）。交出那一刻就已存在的未提交改动单独记成前一个提交，不算在对方头上。登记的素材目录和过大的新文件不收进分支。
- **审计日志**：每次交接、快照、建分支、推送都记在 `state/audit/`，密钥会被打码。

## 需要什么

- macOS 13 及以上，用系统自带的 Python（`/usr/bin/python3`，3.9+），不用装任何包。
- [Claude Code](https://claude.com/claude-code) 作为主力（用到它的钩子和技能）。
- Codex 命令行（ChatGPT 桌面应用里自带）或 Antigravity 命令行（`agy`）至少一个，用你自己的账号登录好。
- 想要菜单栏图标需要 Xcode 命令行工具（`swiftc`）；不装也能用命令行。

## 安装

```bash
git clone https://github.com/fatrabbit29/ai-relay.git
cd ai-relay
sh install.sh
```

`install.sh` 会：拷一份样例配置、把钩子垫片装到 `~/.claude/relay-shim.sh`、装两个 Claude Code 技能、编出 `~/Applications/Relay.app`。要覆盖的文件先备份，不改任何 `settings.json`。之后：

1. 改 `config/projects.json`：登记你的工程（绝对路径）、填你的套餐里真有的模型名、选权限档位。
2. 想让 Claude 被拦住的工程：把 `config/claude-hooks.json` 里的 `hooks` 合并进该工程的 `.claude/settings.json`。
3. 每个工程放一份 `docs/STATE.md`，里面有“下一步”清单。第一项没完成的事就是对方要做的，要写具体。
4. 运行 `/usr/bin/python3 bin/relay status` 检查，然后 `open ~/Applications/Relay.app`。

自测（不调用任何模型）：`/usr/bin/python3 -m unittest discover -s tests -q`

## 权限与安全

样例配置是保守档。可以放宽，但要清楚放掉了什么。

| 配置项 | 保守（样例默认） | 全开 |
|---|---|---|
| `codex.sandbox` | `workspace-write`：只能写工程文件夹 | `danger-full-access`：不设沙箱 |
| `codex.network_access` | `false` | `true` |
| `antigravity.permissions` | `edits`：能改工程里的文件，跑不了命令 | `full`：跳过所有确认，整台电脑 |
| `git.push` | `false`：分支只留本机 | `true`：只推隔离分支 |

全开之后，剩下的保护是“事后能发现、能找回”（快照、清单、隔离分支），不是“事先拦得住”。`config/codex_prompt.md` 和 `config/agy_prompt.md` 里的规矩（不提交、不删除、不动素材、不上传代码和数据）是对模型的嘱咐，不是硬性限制。

## 边界

- 拦截只管装了钩子的工程里的 Claude Code。别的应用或你自己改同一个文件夹，拦不住，只能靠清单对比事后发现。
- 素材目录只读只是一道门槛，同一用户的程序能自己解开。
- 快照放在你配置的那块盘上；和工程同盘的话，盘坏会一起丢。
- 菜单文字跟系统语言走（中文或英文）。**终端输出、弹窗和内置的指令文件目前只有中文。**
- Antigravity 的“额度用完”是靠关键词猜的。
- `bin/media_ask`（请 Gemini 听/看媒体文件）是可选件，其中视频那一路依赖一个未包含在内的私有小工具。
- 样例配置里的模型名是占位符，请换成你的套餐里有的。

## 测过什么

- 113 个自动测试，用假的 Codex/Antigravity 程序：锁、拦截、快照、清单、交接流程、隔离分支、推送失败处理。
- 作者机器上的真实运行：在小测试工程上无人值守交给 Codex、交给 Antigravity（`agy` 1.5.7）、交回、隔离分支推到本机的裸仓库。
- **还没真实测过的：** 推到 GitHub 本身、多段接力、真的把额度用完、大仓库、作者以外的任何机器。

## 服务条款

ai-relay 只是用你自己登录的账号，一次启动一家的官方命令行工具，和你手动操作是一回事。它不共享账号、不代理请求、不绕过限额。无人值守使用是否符合你所购套餐的条款，请自行确认。本项目与 Anthropic、OpenAI、Google 均无关联。

## 卸载

先退出菜单栏图标，然后删掉 `~/Applications/Relay.app`、`~/.claude/relay-shim.sh`、`~/.claude/skills/relay-handoff`、`~/.claude/skills/relay-takeback`、`~/.claude/relay-holders.json`、各工程里合并进去的 `hooks`，以及本文件夹。设过素材只读的，先运行 `relay unprotect <工程>`。

## 许可证

MIT，见 [LICENSE](LICENSE)。不提供任何担保；请自己留好备份。
