# ⚡ AI 接力台 (AI Relay Studio)

**Claude 额度用光了、项目做到一半怎么办？**  
把同一个工程像接力棒一样在 **Claude Code、Gemini (Antigravity)、OpenAI Codex、豆包工作台、DeepSeek** 五家工具之间一键接力：**零上下文断档、零重复消耗 Token、单写锁防覆盖、自动快照与 Git 隔离分支**。

---

## 💡 为什么要做这个工具？（真实痛点）

平时同时跑好几个项目时，最崩溃的瞬间就是：**Claude 额度突然到期了，充值额度也见底了，但手头的项目才做到一半。**

以前切到 **Gemini (Antigravity)**、**Codex**、**豆包** 或 **DeepSeek** 接着干时，总会遇到两个大坑：
1. **换过去断档**：新的 AI 根本不知道前因后果，你得重新花大把 Token 让它读整个项目、重新解释做到哪一步了。
2. **切回来又断档**：等你在别的工具改完、Claude 额度恢复再回来时，Claude 又懵了——还得再消耗一遍 Token 让它重新了解进度，甚至两边同时改文件互相覆盖、把代码改乱。

**「AI 接力台」就是为了彻底解决这个断档问题而生的原生 macOS 桌面小软件：**
- **一键传棒，自动带走上下文**：点一下要接手的 AI，软件自动把「工程结构 + 上一轮刚改了哪些文件 + 下一步任务 (`docs/STATE.md`) + 交接记录 (`BATON.md`)」打包好（支持一键复制到剪贴板直接粘贴，或后台自动调用 CLI / API 接着跑）。
- **同一时间只有一个在写**：交出期间，Claude Code 在该工程下被钩子自动限制为只读，素材目录（`raw/`）自动上系统级只读锁。
- **无痕 Git 隔离分支 + 快照兜底**：接手的 AI 改了什么，交回时用临时索引单独存到 `relay/<工具>/<时间>` 分支，**完全不动你当前的分支和工作区**；每次交出/交回前自动拍快照，随时可一键还原对比。

---

## ✨ 核心特性（桌面增强版 v1.0）

- 🖥 **原生 macOS 桌面应用（SwiftUI）**：独立左右分栏主窗口 + 程序坞（Dock）图标 + 顶部菜单栏快捷入口，告别黑框终端弹窗。
- 🤖 **五大模型全打通（海外 3 家 + 国内 2 家）**：
  - **Claude Code**：主力持棒 + `PreToolUse` / `PostToolUse` / `Stop` 钩子自动记录与只读保护。
  - **Google Antigravity (Gemini)**：支持手动开工（自动拍快照+复制上下文）与 `agy` 命令行后台无人值守分段执行。
  - **OpenAI Codex**：支持 Codex CLI 无人值守分段执行并自动跑单元测试，也支持手动开工。
  - **豆包工作台 (Doubao)**：支持「一键带上下文唤醒豆包工作台（免配 Key，过去直接 `Cmd+V`）」与火山方舟 API 后台自动改代码。
  - **DeepSeek (深度求索)**：支持「一键带上下文唤醒 DeepSeek（免配 Key）」与 `deepseek-chat` / `deepseek-reasoner` API 后台自动改代码。
- 📂 **一键导入工程**：点击「添加工程」选择任意文件夹，自动补齐 `.gitignore`、自动生成 `docs/STATE.md`、自动注入 Claude 钩子，零手动配置。
- 📝 **内置任务编辑器与实时控制台**：在窗口内直接编辑保存下一步任务（`docs/STATE.md`），实时查看快照与执行日志。

---

## 🚀 快速安装与使用

### 环境要求
- macOS 13 及以上（使用系统自带 `/usr/bin/python3` 与 `/usr/bin/swiftc`，**零第三方依赖**，无需 `pip install` 或 `npm install`）。

### 一键安装
```bash
git clone https://github.com/haoxiaolong1102/ai-relay-studio.git
cd ai-relay-studio
sh install.sh
open "$HOME/Applications/Relay.app"
```

### 常用命令行（也可完全在桌面界面点按钮操作）
| 操作 | 桌面界面 | 命令行 |
|---|---|---|
| 一键带上下文去豆包工作台 | 点击「一键带上下文去豆包工作台」 | `relay open --project <代号> --tool doubao` |
| 一键带上下文去 DeepSeek | 点击「一键带上下文去 DeepSeek」 | `relay open --project <代号> --tool deepseek` |
| 交给 Antigravity (Gemini) | 点击「到 Antigravity 手动开工」或「agy 后台跑」 | `relay open --project <代号> --tool antigravity` / `relay handoff --project <代号> --to antigravity` |
| 交给 Codex 自动接着做 | 点击「让 Codex 后台自动接着做」 | `relay handoff --project <代号> --to codex` |
| 收工并交回 Claude | 点击右上角「收工 / 交回 Claude」 | `relay takeback --project <代号>` |
| 运行 113 项自测 | — | `/usr/bin/python3 -m unittest discover -s tests -q` |

---

## ❤️ 觉得好用？打赏支持

这个工具是从我自己在多模型切换时的真实痛点里打磨出来的，完全开源免费分享给大家，同时也会同步上架到我的网站 **合流（[heliuai.com](https://heliuai.com)）**。  
如果它真的帮到了你、帮你省下了时间和 Token，欢迎给仓库点个 **Star ⭐️**，也可以在软件内点击 **「打赏支持」** 或直接微信扫码任意金额赞赏（哪怕几毛、一块钱，都是向我证明这个东西确实好用、确实帮到了大家！）：

<p align="center">
  <img src="menubar/donate_qr.png" width="300" alt="微信赞赏支持 Eason晓龙" />
</p>

---

## 📄 许可证
MIT License（见 [LICENSE](LICENSE)）。基于 [ai-relay](https://github.com/fatrabbit29/ai-relay) 核心底座升级构建。
