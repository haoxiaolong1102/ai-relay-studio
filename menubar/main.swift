// 合流 AI 接力台 (Heliu AI Relay Studio) — 原生 macOS 桌面应用 (SwiftUI + AppKit)
// 品牌官网：https://heliuai.com
// 支持：OpenAI Codex（主力）/ Google Gemini (Antigravity) / 豆包工作台 / DeepSeek / Claude Code 五合一接力
// 支持：对话框级（Thread-to-Thread）精准对接 + 微信任意金额打赏弹窗
import Cocoa
import SwiftUI

let ROOT = ProcessInfo.processInfo.environment["RELAY_ROOT"]
    ?? (Bundle.main.object(forInfoDictionaryKey: "RelayRoot") as? String)
    ?? "/Users/eason/.gemini/antigravity/scratch/ai-relay"
let PY = "/usr/bin/python3"
let RELAY = ROOT + "/bin/relay"
let HELPER = ROOT + "/menubar/relay_state.py"

// MARK: - 数据结构

struct ThreadOption: Identifiable, Equatable, Hashable {
    let id: String
    let tool: String
    let title: String
    let cwd: String
    let updated: String
    let inProject: Bool

    var displayLabel: String {
        let prefix = inProject ? "📌 [本目录] " : "💬 "
        let timeStr = updated.isEmpty ? "" : " (\(updated))"
        return "\(prefix)\(title)\(timeStr) · \(String(id.prefix(8)))"
    }
}

struct ProjectItem: Identifiable, Equatable {
    var id: String { code }
    let code: String
    let name: String
    let path: String
    let holder: String
    let homeHolder: String
    let since: String
    let running: Bool
    let snapshotsCount: Int
    let latestSnapshot: String
    var stateMd: String
    let batonMd: String
    let codexThreads: [ThreadOption]
    let boundCodexThreadId: String
    let boundAgyConvId: String
}

struct SettingsData: Equatable {
    var codexModel: String = "gpt-6.1-sol"
    var agyModel: String = "gemini-4-argon-medium"
    var doubaoModel: String = "doubao-seed-1-6-250615"
    var doubaoApiUrl: String = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
    var doubaoApiKey: String = ""
    var doubaoWebUrl: String = "https://www.doubao.com/chat/"
    var deepseekModel: String = "deepseek-chat"
    var deepseekApiUrl: String = "https://api.deepseek.com/chat/completions"
    var deepseekApiKey: String = ""
    var deepseekWebUrl: String = "https://chat.deepseek.com/"
}

func holderDisplayName(_ h: String) -> String {
    switch h {
    case "claude": return "Claude Code"
    case "codex": return "OpenAI Codex"
    case "antigravity": return "Gemini (Antigravity)"
    case "doubao": return "豆包工作台"
    case "deepseek": return "DeepSeek"
    default: return h
    }
}

func heliuLogoImage() -> NSImage? {
    if let url = Bundle.main.url(forResource: "heliu_logo", withExtension: "png"),
       let img = NSImage(contentsOf: url) {
        return img
    }
    return NSImage(contentsOfFile: ROOT + "/menubar/heliu_logo.png")
}

func holderColor(_ h: String) -> Color {
    switch h {
    case "claude": return Color(red: 0.85, green: 0.46, blue: 0.34)
    case "codex": return Color(red: 0.16, green: 0.64, blue: 0.50)
    case "antigravity": return Color(red: 0.26, green: 0.48, blue: 0.92)
    case "doubao": return Color(red: 0.20, green: 0.68, blue: 0.95)
    case "deepseek": return Color(red: 0.42, green: 0.36, blue: 0.92)
    default: return .gray
    }
}

func runProcess(_ args: [String], envExtra: [String: String] = [:], timeout: TimeInterval = 120) -> (Int32, String) {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: args[0])
    p.arguments = Array(args.dropFirst())
    var env = ProcessInfo.processInfo.environment
    env["RELAY_NO_POPUP"] = "1"
    for (k, v) in envExtra { env[k] = v }
    p.environment = env
    let pipe = Pipe()
    p.standardOutput = pipe
    p.standardError = pipe
    do { try p.run() } catch { return (-1, error.localizedDescription) }
    let killer = DispatchWorkItem { if p.isRunning { p.terminate() } }
    DispatchQueue.global().asyncAfter(deadline: .now() + timeout, execute: killer)
    let data = pipe.fileHandleForReading.readDataToEndOfFile()
    p.waitUntilExit()
    killer.cancel()
    return (p.terminationStatus, String(data: data, encoding: .utf8) ?? "")
}

// MARK: - 核心状态管理 Store

final class RelayStore: ObservableObject {
    @Published var liveEnabled: Bool = true
    @Published var projects: [ProjectItem] = []
    @Published var agyConversations: [ThreadOption] = []
    @Published var selectedCode: String? = nil
    @Published var selectedCodexThreadId: String = ""
    @Published var selectedAgyConvId: String = ""
    @Published var diffs: [String] = []
    @Published var logs: [String] = []
    @Published var consoleOutput: String = "欢迎使用【合流 AI 接力台 (heliuai.com)】！\n当前默认主力：OpenAI Codex。支持按单个【对话框】精准接力到 Gemini (Antigravity)、豆包工作台、DeepSeek、Claude Code。\n"
    @Published var isBusy: Bool = false
    @Published var busyTitle: String = ""
    @Published var bannerMessage: String? = nil
    @Published var showDonateSheet: Bool = false
    @Published var showSettingsSheet: Bool = false
    @Published var settings: SettingsData = SettingsData()
    @Published var editedStateMd: String = ""
    @Published var stateDirty: Bool = false

    var actionCount: Int = 0
    var hasPromptedDonate: Bool = false
    var onStateUpdated: (() -> Void)?

    var selectedProject: ProjectItem? {
        guard let code = selectedCode else { return projects.first }
        return projects.first(where: { $0.code == code }) ?? projects.first
    }

    func refresh(syncEditor: Bool = false) {
        DispatchQueue.global(qos: .userInitiated).async {
            let (_, out) = runProcess([PY, HELPER, "json"], timeout: 15)
            DispatchQueue.main.async {
                self.applyJson(out, syncEditor: syncEditor)
            }
        }
    }

    func applyJson(_ out: String, syncEditor: Bool = false) {
        guard let line = out.split(separator: "\n").last,
              let obj = try? JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any],
              (obj["ok"] as? Bool) == true else {
            return
        }
        self.liveEnabled = (obj["live"] as? Bool) ?? true
        self.diffs = (obj["diffs"] as? [String]) ?? []
        self.logs = (obj["logs"] as? [String]) ?? []

        var parsedAgy: [ThreadOption] = []
        for a in (obj["agy_conversations"] as? [[String: Any]]) ?? [] {
            parsedAgy.append(ThreadOption(
                id: a["id"] as? String ?? "",
                tool: "antigravity",
                title: a["title"] as? String ?? "Gemini 会话",
                cwd: "",
                updated: a["updated"] as? String ?? "",
                inProject: false
            ))
        }
        self.agyConversations = parsedAgy

        var parsed: [ProjectItem] = []
        for p in (obj["projects"] as? [[String: Any]]) ?? [] {
            var cThreads: [ThreadOption] = []
            for t in (p["codex_threads"] as? [[String: Any]]) ?? [] {
                cThreads.append(ThreadOption(
                    id: t["id"] as? String ?? "",
                    tool: "codex",
                    title: t["title"] as? String ?? "Codex 会话",
                    cwd: t["cwd"] as? String ?? "",
                    updated: t["updated"] as? String ?? "",
                    inProject: t["in_project"] as? Bool ?? false
                ))
            }
            let bind = (p["binding"] as? [String: Any]) ?? [:]
            parsed.append(ProjectItem(
                code: p["code"] as? String ?? "?",
                name: p["name"] as? String ?? "?",
                path: p["path"] as? String ?? "",
                holder: p["holder"] as? String ?? "codex",
                homeHolder: p["home_holder"] as? String ?? "codex",
                since: p["since"] as? String ?? "",
                running: p["running"] as? Bool ?? false,
                snapshotsCount: p["snapshots_count"] as? Int ?? 0,
                latestSnapshot: p["latest_snapshot"] as? String ?? "",
                stateMd: p["state_md"] as? String ?? "",
                batonMd: p["baton_md"] as? String ?? "",
                codexThreads: cThreads,
                boundCodexThreadId: bind["codex_thread_id"] as? String ?? "",
                boundAgyConvId: bind["agy_conv_id"] as? String ?? ""
            ))
        }
        let oldSelected = self.selectedCode
        self.projects = parsed
        if self.selectedCode == nil || !parsed.contains(where: { $0.code == self.selectedCode }) {
            self.selectedCode = parsed.first?.code
        }
        if syncEditor || self.selectedCode != oldSelected || !self.stateDirty {
            if let cur = self.selectedProject {
                self.editedStateMd = cur.stateMd
                self.stateDirty = false
                if !cur.boundCodexThreadId.isEmpty {
                    self.selectedCodexThreadId = cur.boundCodexThreadId
                } else if self.selectedCodexThreadId.isEmpty || !cur.codexThreads.contains(where: { $0.id == self.selectedCodexThreadId }) {
                    self.selectedCodexThreadId = cur.codexThreads.first?.id ?? ""
                }
                if !cur.boundAgyConvId.isEmpty {
                    self.selectedAgyConvId = cur.boundAgyConvId
                }
            }
        }
        if let st = obj["settings"] as? [String: Any] {
            var s = SettingsData()
            s.codexModel = st["codex_model"] as? String ?? s.codexModel
            s.agyModel = st["agy_model"] as? String ?? s.agyModel
            s.doubaoModel = st["doubao_model"] as? String ?? s.doubaoModel
            s.doubaoApiUrl = st["doubao_api_url"] as? String ?? s.doubaoApiUrl
            s.doubaoApiKey = st["doubao_api_key"] as? String ?? ""
            s.doubaoWebUrl = st["doubao_web_url"] as? String ?? s.doubaoWebUrl
            s.deepseekModel = st["deepseek_model"] as? String ?? s.deepseekModel
            s.deepseekApiUrl = st["deepseek_api_url"] as? String ?? s.deepseekApiUrl
            s.deepseekApiKey = st["deepseek_api_key"] as? String ?? ""
            s.deepseekWebUrl = st["deepseek_web_url"] as? String ?? s.deepseekWebUrl
            self.settings = s
        }
        self.onStateUpdated?()
    }

    func selectProject(_ code: String) {
        self.selectedCode = code
        if let cur = projects.first(where: { $0.code == code }) {
            self.editedStateMd = cur.stateMd
            self.stateDirty = false
            self.selectedCodexThreadId = !cur.boundCodexThreadId.isEmpty ? cur.boundCodexThreadId : (cur.codexThreads.first?.id ?? "")
            self.selectedAgyConvId = cur.boundAgyConvId
        }
    }

    func bindThreads(codexId: String, agyId: String) {
        guard let proj = selectedProject else { return }
        DispatchQueue.global().async {
            let (_, out) = runProcess([PY, HELPER, "bind-threads", proj.code, codexId, agyId])
            DispatchQueue.main.async {
                self.applyJson(out)
                self.bannerMessage = "🔗 已绑定对话框：Codex (\(codexId.prefix(8))) ↔ Gemini (\(agyId.isEmpty ? "新建/任意" : String(agyId.prefix(8))))"
            }
        }
    }

    func copyThreadPrompt(targetToolName: String = "Gemini (Antigravity)") {
        guard let proj = selectedProject else { return }
        if stateDirty { saveStateMd() }
        let srcTid = selectedCodexThreadId
        if srcTid.isEmpty {
            copyPrompt(toolName: targetToolName)
            return
        }
        DispatchQueue.global().async {
            let (_, _) = runProcess([PY, HELPER, "copy-thread-prompt", proj.code, "codex", srcTid, targetToolName])
            DispatchQueue.main.async {
                let title = proj.codexThreads.first(where: { $0.id == srcTid })?.title ?? String(srcTid.prefix(8))
                self.bannerMessage = "📋 已仅提取【\(title)】单个对话框的上下文并复制到剪贴板！过去直接 Cmd+V 即可！"
                self.appendLog("已精准提取 Codex 对话框「\(title)」(\(srcTid.prefix(8))) 的专属上下文到剪贴板。")
            }
        }
    }

    func setBaseHolder(_ holder: String) {
        guard let proj = selectedProject else { return }
        DispatchQueue.global().async {
            let (_, out) = runProcess([PY, HELPER, "set-holder", proj.code, holder])
            DispatchQueue.main.async {
                self.applyJson(out)
                self.bannerMessage = "✅ 已将「\(proj.name)」的主力持棒工具切换为：\(holderDisplayName(holder))"
                self.appendLog("切换主力持棒工具为：\(holderDisplayName(holder))")
            }
        }
    }

    func appendLog(_ text: String) {
        let stamp = DateFormatter.localizedString(from: Date(), dateStyle: .none, timeStyle: .medium)
        self.consoleOutput += "\n[\(stamp)] \(text.trimmingCharacters(in: .whitespacesAndNewlines))\n"
    }

    func maybeTriggerDonatePopup() {
        actionCount += 1
        if !hasPromptedDonate && actionCount >= 1 {
            hasPromptedDonate = true
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) {
                self.showDonateSheet = true
            }
        }
    }

    func toggleLive(_ on: Bool) {
        DispatchQueue.global().async {
            let (_, out) = runProcess([PY, HELPER, "live", on ? "on" : "off"])
            DispatchQueue.main.async {
                self.applyJson(out)
                self.appendLog("接力总开关已切为：\(on ? "开启" : "关闭")")
            }
        }
    }

    func saveStateMd() {
        guard let proj = selectedProject else { return }
        let text = editedStateMd
        DispatchQueue.global().async {
            let (_, out) = runProcess([PY, HELPER, "save-state", proj.code, text])
            DispatchQueue.main.async {
                self.stateDirty = false
                self.applyJson(out)
                self.bannerMessage = "✅ 已保存「\(proj.name)」的下一步任务 (docs/STATE.md)"
                self.appendLog("已保存 \(proj.name) 的 docs/STATE.md")
            }
        }
    }

    func copyPrompt(toolName: String = "AI 助手") {
        guard let proj = selectedProject else { return }
        if stateDirty { saveStateMd() }
        DispatchQueue.global().async {
            let (_, _) = runProcess([PY, HELPER, "copy-prompt", proj.code, toolName])
            DispatchQueue.main.async {
                self.bannerMessage = "📋 已将「\(proj.name)」的完整接力提示词复制到剪贴板，可直接 Cmd+V 粘贴！"
                self.appendLog("已复制「\(proj.name)」面向 \(toolName) 的上下文提示词到系统剪贴板。")
            }
        }
    }

    func runRelayCommand(title: String, args: [String], triggerDonateAfter: Bool = false) {
        guard !isBusy else { return }
        if stateDirty { saveStateMd() }
        isBusy = true
        busyTitle = title
        bannerMessage = "⏳ 正在执行：\(title)…"
        appendLog("开始执行：\(title)")
        DispatchQueue.global(qos: .userInitiated).async {
            let (rc, out) = runProcess([PY, RELAY] + args, timeout: 600)
            DispatchQueue.main.async {
                self.isBusy = false
                self.busyTitle = ""
                let clean = out.trimmingCharacters(in: .whitespacesAndNewlines)
                self.appendLog(clean.isEmpty ? "完成（退出码 \(rc)）" : clean)
                if rc == 0 {
                    self.bannerMessage = "✅ \(title) 完成！"
                    if triggerDonateAfter {
                        self.maybeTriggerDonatePopup()
                    }
                } else {
                    self.bannerMessage = "⚠️ \(title) 未完成，请查看下方日志说明"
                }
                self.refresh(syncEditor: true)
            }
        }
    }

    func addProjectInteractive() {
        let panel = NSOpenPanel()
        panel.title = "选择要接入 AI 接力台的工程文件夹"
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "一键接入"
        if panel.runModal() == .OK, let url = panel.url {
            let folderPath = url.path
            let folderName = url.lastPathComponent
            isBusy = true
            busyTitle = "正在初始化工程 \(folderName)…"
            DispatchQueue.global().async {
                let (_, out) = runProcess([PY, HELPER, "add-project", folderName, folderPath])
                DispatchQueue.main.async {
                    self.isBusy = false
                    self.busyTitle = ""
                    self.applyJson(out, syncEditor: true)
                    if let last = self.projects.last {
                        self.selectProject(last.code)
                    }
                    self.bannerMessage = "✅ 已一键接入工程「\(folderName)」（已自动配置 .gitignore、STATE.md 与 Claude 钩子）"
                    self.appendLog("成功导入工程：\(folderName) (\(folderPath))")
                }
            }
        }
    }

    func removeProject(_ code: String) {
        DispatchQueue.global().async {
            let (_, out) = runProcess([PY, HELPER, "remove-project", code])
            DispatchQueue.main.async {
                self.applyJson(out, syncEditor: true)
                self.bannerMessage = "已从列表移除工程 \(code)（原工程文件未做任何删除）"
            }
        }
    }

    func saveSettings() {
        let dict: [String: String] = [
            "codex_model": settings.codexModel,
            "agy_model": settings.agyModel,
            "doubao_model": settings.doubaoModel,
            "doubao_api_url": settings.doubaoApiUrl,
            "doubao_api_key": settings.doubaoApiKey,
            "doubao_web_url": settings.doubaoWebUrl,
            "deepseek_model": settings.deepseekModel,
            "deepseek_api_url": settings.deepseekApiUrl,
            "deepseek_api_key": settings.deepseekApiKey,
            "deepseek_web_url": settings.deepseekWebUrl,
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: dict),
              let jsonStr = String(data: data, encoding: .utf8) else { return }
        DispatchQueue.global().async {
            let (_, out) = runProcess([PY, HELPER, "save-settings", jsonStr])
            DispatchQueue.main.async {
                self.applyJson(out)
                self.showSettingsSheet = false
                self.bannerMessage = "✅ 模型与 API 设置已保存！"
                self.appendLog("已更新模型与国内大模型 API 设置。")
            }
        }
    }
}

// MARK: - 打赏弹窗视图（使用用户上传的微信收款码）

struct DonateSheetView: View {
    @EnvironmentObject var store: RelayStore

    var qrImage: NSImage? {
        if let url = Bundle.main.url(forResource: "donate_qr", withExtension: "png"),
           let img = NSImage(contentsOf: url) {
            return img
        }
        let fallbackPath = ROOT + "/menubar/donate_qr.png"
        return NSImage(contentsOfFile: fallbackPath)
    }

    var body: some View {
        VStack(spacing: 16) {
            HStack {
                Label("觉得好用？打赏支持作者", systemImage: "heart.fill")
                    .font(.system(size: 18, weight: .bold))
                    .foregroundColor(Color(red: 0.09, green: 0.68, blue: 0.31))
                Spacer()
                Button(action: { store.showDonateSheet = false }) {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 18))
                        .foregroundColor(.secondary)
                }
                .buttonStyle(.plain)
            }

            Text("如果「AI 接力台」帮你在 Claude、Codex、Antigravity、豆包、DeepSeek 之间省下了时间、保护了代码安全，欢迎微信扫码任意金额打赏支持！")
                .font(.system(size: 13))
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)

            if let img = qrImage {
                Image(nsImage: img)
                    .resizable()
                    .interpolation(.high)
                    .scaledToFit()
                    .frame(width: 280, height: 340)
                    .clipShape(RoundedRectangle(cornerRadius: 14))
                    .shadow(color: Color.black.opacity(0.12), radius: 8, x: 0, y: 4)
            } else {
                Text("未找到收款码图片")
                    .frame(width: 240, height: 240)
            }

            Text("任意金额都是莫大的鼓励，感谢你的支持 ❤️")
                .font(.system(size: 12, weight: .medium))
                .foregroundColor(.secondary)

            HStack(spacing: 14) {
                Button("下次一定") {
                    store.showDonateSheet = false
                }
                .keyboardShortcut(.cancelAction)

                Button(action: {
                    store.showDonateSheet = false
                    store.bannerMessage = "❤️ 非常感谢你的赞赏与支持！祝你写代码行云流水！"
                }) {
                    HStack(spacing: 6) {
                        Image(systemName: "hand.thumbsup.fill")
                        Text("已赞赏，继续干活！")
                    }
                    .padding(.horizontal, 12)
                    .padding(.vertical, 4)
                }
                .buttonStyle(.borderedProminent)
                .tint(Color(red: 0.09, green: 0.68, blue: 0.31))
                .keyboardShortcut(.defaultAction)
            }
            .padding(.top, 4)
        }
        .padding(24)
        .frame(width: 420)
    }
}

// MARK: - 模型与 API 设置弹窗

struct SettingsSheetView: View {
    @EnvironmentObject var store: RelayStore

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Label("五家大模型与 API 设置", systemImage: "slider.horizontal.3")
                    .font(.system(size: 17, weight: .bold))
                Spacer()
                Button("关闭") { store.showSettingsSheet = false }
            }

            Divider()

            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    GroupBox(label: Label("豆包工作台 (Doubao / 火山方舟)", systemImage: "sparkles")) {
                        VStack(alignment: .leading, spacing: 8) {
                            HStack {
                                Text("工作台网址：").frame(width: 95, alignment: .trailing)
                                TextField("https://www.doubao.com/chat/", text: $store.settings.doubaoWebUrl)
                            }
                            HStack {
                                Text("API 接入点/模型：").frame(width: 95, alignment: .trailing)
                                TextField("doubao-seed-1-6-250615", text: $store.settings.doubaoModel)
                            }
                            HStack {
                                Text("API Key（可选）：").frame(width: 95, alignment: .trailing)
                                SecureField("不填可用“一键带上下文去豆包工作台”；填了可后台自动改文件", text: $store.settings.doubaoApiKey)
                            }
                        }
                        .padding(8)
                    }

                    GroupBox(label: Label("DeepSeek (深度求索)", systemImage: "brain.head.profile")) {
                        VStack(alignment: .leading, spacing: 8) {
                            HStack {
                                Text("工作台网址：").frame(width: 95, alignment: .trailing)
                                TextField("https://chat.deepseek.com/", text: $store.settings.deepseekWebUrl)
                            }
                            HStack {
                                Text("模型名称：").frame(width: 95, alignment: .trailing)
                                TextField("deepseek-chat 或 deepseek-reasoner", text: $store.settings.deepseekModel)
                            }
                            HStack {
                                Text("API Key（可选）：").frame(width: 95, alignment: .trailing)
                                SecureField("不填可用“一键带上下文去 DeepSeek”；填了可后台自动改文件", text: $store.settings.deepseekApiKey)
                            }
                        }
                        .padding(8)
                    }

                    GroupBox(label: Label("海外命令行工具 (Codex & Antigravity)", systemImage: "terminal")) {
                        VStack(alignment: .leading, spacing: 8) {
                            HStack {
                                Text("Codex 模型：").frame(width: 95, alignment: .trailing)
                                TextField("gpt-6.1-sol", text: $store.settings.codexModel)
                            }
                            HStack {
                                Text("Antigravity 模型：").frame(width: 95, alignment: .trailing)
                                TextField("gemini-4-argon-medium", text: $store.settings.agyModel)
                            }
                        }
                        .padding(8)
                    }
                }
            }

            HStack {
                Text("提示：使用“一键带上下文去工作台”完全免费、无需填写任何 API Key。")
                    .font(.system(size: 11))
                    .foregroundColor(.secondary)
                Spacer()
                Button("取消") { store.showSettingsSheet = false }
                Button("保存设置") { store.saveSettings() }
                    .buttonStyle(.borderedProminent)
            }
        }
        .padding(20)
        .frame(width: 560, height: 470)
    }
}

// MARK: - 主窗口视图

struct ToolCardView: View {
    let title: String
    let subtitle: String
    let badge: String
    let color: Color
    let isCurrentHolder: Bool
    let canHandOff: Bool
    let primaryLabel: String
    let primaryAction: () -> Void
    let secondaryLabel: String?
    let secondaryAction: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Circle()
                    .fill(color)
                    .frame(width: 10, height: 10)
                Text(title)
                    .font(.system(size: 14, weight: .bold))
                Spacer()
                Text(isCurrentHolder ? "当前持棒" : badge)
                    .font(.system(size: 10, weight: .semibold))
                    .padding(.horizontal, 7)
                    .padding(.vertical, 2)
                    .background((isCurrentHolder ? color : Color.secondary).opacity(0.16))
                    .foregroundColor(isCurrentHolder ? color : .secondary)
                    .clipShape(Capsule())
            }

            Text(subtitle)
                .font(.system(size: 11.5))
                .foregroundColor(.secondary)
                .lineLimit(2)
                .frame(height: 30, alignment: .topLeading)

            HStack(spacing: 8) {
                Button(action: primaryAction) {
                    Text(primaryLabel)
                        .font(.system(size: 12, weight: .medium))
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .tint(color)
                .disabled(!canHandOff)

                if let secLabel = secondaryLabel, let secAct = secondaryAction {
                    Button(action: secAct) {
                        Text(secLabel)
                            .font(.system(size: 11.5))
                    }
                    .buttonStyle(.bordered)
                    .disabled(!canHandOff)
                }
            }
        }
        .padding(12)
        .background(
            RoundedRectangle(cornerRadius: 10)
                .fill(Color(NSColor.controlBackgroundColor))
                .overlay(
                    RoundedRectangle(cornerRadius: 10)
                        .stroke(isCurrentHolder ? color : Color.gray.opacity(0.2), lineWidth: isCurrentHolder ? 2 : 1)
                )
        )
    }
}

struct MainWindowView: View {
    @EnvironmentObject var store: RelayStore

    var body: some View {
        NavigationSplitView {
            // 左侧栏：合流品牌头 + 工程列表 + 总开关 + 打赏入口
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 10) {
                    if let logo = heliuLogoImage() {
                        Image(nsImage: logo)
                            .resizable()
                            .interpolation(.high)
                            .scaledToFit()
                            .frame(width: 30, height: 30)
                            .clipShape(RoundedRectangle(cornerRadius: 7))
                    }
                    VStack(alignment: .leading, spacing: 1) {
                        Text("合流 AI 接力台")
                            .font(.system(size: 14, weight: .bold))
                        Text("heliuai.com · 对话级接力")
                            .font(.system(size: 10.5))
                            .foregroundColor(.secondary)
                    }
                    Spacer()
                }
                .padding(.horizontal, 14)
                .padding(.top, 12)
                .padding(.bottom, 6)

                Divider()

                HStack {
                    Text("我的接力工程")
                        .font(.system(size: 12.5, weight: .bold))
                        .foregroundColor(.secondary)
                    Spacer()
                    Button(action: { store.addProjectInteractive() }) {
                        Label("添加", systemImage: "plus.circle.fill")
                            .font(.system(size: 12, weight: .semibold))
                    }
                    .buttonStyle(.borderless)
                    .help("选择一个工程文件夹，自动初始化并加入接力列表")
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 8)

                List(selection: Binding(
                    get: { store.selectedProject?.code },
                    set: { if let c = $0 { store.selectProject(c) } }
                )) {
                    ForEach(store.projects) { p in
                        HStack(spacing: 8) {
                            Circle()
                                .fill(holderColor(p.holder))
                                .frame(width: 9, height: 9)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(p.name)
                                    .font(.system(size: 13, weight: .medium))
                                    .lineLimit(1)
                                Text("持棒：\(holderDisplayName(p.holder))\(p.running ? "（运行中）" : "")")
                                    .font(.system(size: 11))
                                    .foregroundColor(.secondary)
                            }
                            Spacer()
                        }
                        .padding(.vertical, 3)
                        .tag(p.code)
                        .contextMenu {
                            Button("在 Finder 中打开") {
                                NSWorkspace.shared.selectFile(nil, inFileViewerRootedAtPath: p.path)
                            }
                            Button("从列表中移除", role: .destructive) {
                                store.removeProject(p.code)
                            }
                        }
                    }
                }
                .listStyle(.sidebar)

                Divider()

                VStack(alignment: .leading, spacing: 10) {
                    Toggle(isOn: Binding(
                        get: { store.liveEnabled },
                        set: { store.toggleLive($0) }
                    )) {
                        Text("接力总开关（拦截保护）")
                            .font(.system(size: 12, weight: .medium))
                    }
                    .toggleStyle(.switch)

                    HStack(spacing: 8) {
                        Button("修复锁状态") {
                            store.runRelayCommand(title: "修复锁与抄本状态", args: ["repair"])
                        }
                        .font(.system(size: 11))

                        Button("模型设置") {
                            store.showSettingsSheet = true
                        }
                        .font(.system(size: 11))
                    }

                    Button(action: { store.showDonateSheet = true }) {
                        HStack {
                            Image(systemName: "heart.fill")
                                .foregroundColor(.white)
                            Text("觉得好用？任意金额打赏")
                                .font(.system(size: 12, weight: .semibold))
                                .foregroundColor(.white)
                            Spacer()
                        }
                        .padding(.horizontal, 10)
                        .padding(.vertical, 7)
                        .background(
                            LinearGradient(
                                colors: [Color(red: 0.10, green: 0.72, blue: 0.36), Color(red: 0.06, green: 0.56, blue: 0.28)],
                                startPoint: .leading,
                                endPoint: .trailing
                            )
                        )
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                    .buttonStyle(.plain)
                }
                .padding(12)
            }
            .frame(minWidth: 230, idealWidth: 250, maxWidth: 280)
        } detail: {
            if let proj = store.selectedProject {
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        // 顶部通知条
                        if let msg = store.bannerMessage {
                            HStack {
                                Text(msg)
                                    .font(.system(size: 12.5, weight: .medium))
                                Spacer()
                                Button("关闭") { store.bannerMessage = nil }
                                    .buttonStyle(.borderless)
                                    .font(.system(size: 11))
                            }
                            .padding(.horizontal, 12)
                            .padding(.vertical, 8)
                            .background(Color.accentColor.opacity(0.12))
                            .clipShape(RoundedRectangle(cornerRadius: 8))
                        }

                        // 工程头部卡片
                        HStack(alignment: .center, spacing: 14) {
                            VStack(alignment: .leading, spacing: 5) {
                                HStack(spacing: 10) {
                                    Text(proj.name)
                                        .font(.system(size: 20, weight: .bold))
                                    Text("当前持棒：\(holderDisplayName(proj.holder))")
                                        .font(.system(size: 12, weight: .bold))
                                        .padding(.horizontal, 10)
                                        .padding(.vertical, 4)
                                        .background(holderColor(proj.holder).opacity(0.18))
                                        .foregroundColor(holderColor(proj.holder))
                                        .clipShape(Capsule())
                                    if proj.running {
                                        ProgressView()
                                            .controlSize(.small)
                                        Text("后台接力运行中…")
                                            .font(.system(size: 12))
                                            .foregroundColor(.orange)
                                    }
                                }
                                HStack(spacing: 12) {
                                    Text(proj.path)
                                        .font(.system(size: 11.5, design: .monospaced))
                                        .foregroundColor(.secondary)
                                        .lineLimit(1)
                                    Button("打开文件夹") {
                                        NSWorkspace.shared.selectFile(nil, inFileViewerRootedAtPath: proj.path)
                                    }
                                    .font(.system(size: 11))
                                    .buttonStyle(.link)
                                    Text("｜ 快照 \(proj.snapshotsCount) 份")
                                        .font(.system(size: 11.5))
                                        .foregroundColor(.secondary)
                                    Text("｜ 日常主力：")
                                        .font(.system(size: 11.5))
                                        .foregroundColor(.secondary)
                                    Button(proj.homeHolder == "codex" ? "OpenAI Codex (点击可切 Claude)" : "Claude Code (点击可切 Codex)") {
                                        store.setBaseHolder(proj.homeHolder == "codex" ? "claude" : "codex")
                                    }
                                    .font(.system(size: 11, weight: .semibold))
                                    .buttonStyle(.link)
                                }
                            }

                            Spacer()

                            if proj.holder != proj.homeHolder {
                                let backName = holderDisplayName(proj.homeHolder)
                                Button(action: {
                                    store.runRelayCommand(
                                        title: "收工并交回 \(backName) (\(proj.name))",
                                        args: ["takeback", "--project", proj.code, "--to", proj.homeHolder],
                                        triggerDonateAfter: true
                                    )
                                }) {
                                    Label("收工 / 交回 \(backName)", systemImage: "arrow.uturn.backward.circle.fill")
                                        .font(.system(size: 13, weight: .bold))
                                        .padding(.horizontal, 10)
                                        .padding(.vertical, 5)
                                }
                                .buttonStyle(.borderedProminent)
                                .tint(holderColor(proj.homeHolder))
                                .disabled(store.isBusy)
                            } else {
                                Button(action: {
                                    store.copyThreadPrompt(targetToolName: "Gemini (Antigravity)")
                                }) {
                                    Label("仅复制选中对话框上下文", systemImage: "doc.on.doc")
                                        .font(.system(size: 12))
                                }
                                .buttonStyle(.bordered)
                            }
                        }
                        .padding(14)
                        .background(RoundedRectangle(cornerRadius: 12).fill(Color(NSColor.controlBackgroundColor)))

                        // 💬 对话框级精准对接（解决一个工程文件夹下跑多个项目的问题）
                        VStack(alignment: .leading, spacing: 10) {
                            HStack {
                                Label("💬 对话框精准对接（同一个目录跑多个子项目时，只转交选中的那一个对话）", systemImage: "bubble.left.and.bubble.right.fill")
                                    .font(.system(size: 13.5, weight: .bold))
                                    .foregroundColor(Color(red: 0.16, green: 0.64, blue: 0.50))
                                Spacer()
                                Button("刷新对话列表") {
                                    store.refresh(syncEditor: false)
                                }
                                .controlSize(.small)
                            }

                            HStack(spacing: 12) {
                                VStack(alignment: .leading, spacing: 4) {
                                    Text("① 选择要接力的 OpenAI Codex 对话框（含历史提问与进度）：")
                                        .font(.system(size: 11.5, weight: .semibold))
                                        .foregroundColor(.secondary)
                                    Picker("", selection: Binding(
                                        get: { store.selectedCodexThreadId },
                                        set: {
                                            store.selectedCodexThreadId = $0
                                            store.bindThreads(codexId: $0, agyId: store.selectedAgyConvId)
                                        }
                                    )) {
                                        Text("📁 不指定单对话（默认传递整个目录状态）").tag("")
                                        ForEach(proj.codexThreads) { t in
                                            Text(t.displayLabel).tag(t.id)
                                        }
                                    }
                                    .labelsHidden()
                                }

                                Image(systemName: "arrow.right.circle.fill")
                                    .font(.system(size: 18))
                                    .foregroundColor(.secondary)
                                    .padding(.top, 16)

                                VStack(alignment: .leading, spacing: 4) {
                                    Text("② 对接的 Google Gemini (Antigravity) 目标对话框：")
                                        .font(.system(size: 11.5, weight: .semibold))
                                        .foregroundColor(.secondary)
                                    Picker("", selection: Binding(
                                        get: { store.selectedAgyConvId },
                                        set: {
                                            store.selectedAgyConvId = $0
                                            store.bindThreads(codexId: store.selectedCodexThreadId, agyId: $0)
                                        }
                                    )) {
                                        Text("✨ 新建对话 / 手动 Cmd+V 粘贴到任意窗口").tag("")
                                        ForEach(store.agyConversations) { c in
                                            Text("💎 \(c.title) (\(c.updated)) · \(String(c.id.prefix(8)))").tag(c.id)
                                        }
                                    }
                                    .labelsHidden()
                                }
                            }

                            HStack(spacing: 10) {
                                Button(action: {
                                    store.copyThreadPrompt(targetToolName: "Gemini (Antigravity)")
                                }) {
                                    Label("📋 一键提取并复制该 Codex 对话专属上下文", systemImage: "doc.on.clipboard.fill")
                                        .font(.system(size: 12, weight: .medium))
                                }
                                .buttonStyle(.bordered)

                                Text("提示：选好上方 Codex 对话后，点下方任意工具卡片即可只将该对话的上下文、涉及文件与进度精准移交过去！")
                                    .font(.system(size: 11))
                                    .foregroundColor(.secondary)
                                Spacer()
                            }
                        }
                        .padding(12)
                        .background(
                            RoundedRectangle(cornerRadius: 10)
                                .fill(Color(red: 0.16, green: 0.64, blue: 0.50).opacity(0.07))
                                .overlay(
                                    RoundedRectangle(cornerRadius: 10)
                                        .stroke(Color(red: 0.16, green: 0.64, blue: 0.50).opacity(0.30), lineWidth: 1)
                                )
                        )

                        // 五家 AI 接力矩阵（海外 + 国内）
                        VStack(alignment: .leading, spacing: 10) {
                            HStack {
                                Text("把接力棒交给谁？（一键拍快照 + 锁定 \(holderDisplayName(proj.homeHolder)) + 传递选中对话框上下文）")
                                    .font(.system(size: 14, weight: .bold))
                                Spacer()
                                if proj.holder != proj.homeHolder {
                                    Text("提示：当前在 \(holderDisplayName(proj.holder)) 手里，点右上角“收工 / 交回 \(holderDisplayName(proj.homeHolder))”后可再传给别家")
                                        .font(.system(size: 11.5))
                                        .foregroundColor(.orange)
                                }
                            }

                            let canGive = (proj.holder == proj.homeHolder) && !proj.running && store.liveEnabled && !store.isBusy
                            let srcArgs = store.selectedCodexThreadId.isEmpty ? [] : ["--source-thread", store.selectedCodexThreadId]
                            let tgtArgs = store.selectedAgyConvId.isEmpty ? [] : ["--target-thread", store.selectedAgyConvId]

                            LazyVGrid(columns: [GridItem(.flexible(), spacing: 12), GridItem(.flexible(), spacing: 12)], spacing: 12) {
                                // 1. Google Gemini (Antigravity) — 当 Codex 额度用完时的首选接力对象
                                ToolCardView(
                                    title: "Google Gemini (Antigravity)",
                                    subtitle: "Codex 额度用完首选！自动拍快照、提取上方选中的 Codex 对话框上下文复制到剪贴板，或调用 agy 直接续写指定 Gemini 对话。",
                                    badge: "海外主力接力 · " + store.settings.agyModel,
                                    color: holderColor("antigravity"),
                                    isCurrentHolder: proj.holder == "antigravity",
                                    canHandOff: canGive && proj.homeHolder != "antigravity",
                                    primaryLabel: "带此对话去 Gemini 开工",
                                    primaryAction: {
                                        store.runRelayCommand(
                                            title: "将选中对话交给 Gemini (\(proj.name))",
                                            args: ["open", "--project", proj.code, "--tool", "antigravity"] + srcArgs + tgtArgs,
                                            triggerDonateAfter: true
                                        )
                                    },
                                    secondaryLabel: "agy 自动续写",
                                    secondaryAction: {
                                        store.runRelayCommand(
                                            title: "交给 Gemini 后台自动执行 (\(proj.name))",
                                            args: ["handoff", "--project", proj.code, "--to", "antigravity"] + srcArgs + tgtArgs,
                                            triggerDonateAfter: true
                                        )
                                    }
                                )

                                // 2. 豆包工作台
                                ToolCardView(
                                    title: "豆包工作台 (Doubao)",
                                    subtitle: "自动拍快照、锁住原工具、仅复制选中对话框的专属上下文到剪贴板并打开豆包工作台，直接 Cmd+V 粘贴即可接着干。",
                                    badge: "国内主力 · 免配置",
                                    color: holderColor("doubao"),
                                    isCurrentHolder: proj.holder == "doubao",
                                    canHandOff: canGive,
                                    primaryLabel: "一键带此对话去豆包工作台",
                                    primaryAction: {
                                        store.runRelayCommand(
                                            title: "交给豆包工作台 (\(proj.name))",
                                            args: ["open", "--project", proj.code, "--tool", "doubao"] + srcArgs,
                                            triggerDonateAfter: true
                                        )
                                    },
                                    secondaryLabel: "API 自动跑",
                                    secondaryAction: {
                                        store.runRelayCommand(
                                            title: "豆包 API 后台接力 (\(proj.name))",
                                            args: ["handoff", "--project", proj.code, "--to", "doubao"] + srcArgs,
                                            triggerDonateAfter: true
                                        )
                                    }
                                )

                                // 3. DeepSeek
                                ToolCardView(
                                    title: "DeepSeek (深度求索)",
                                    subtitle: "自动拍快照、提取选中对话框上下文复制到剪贴板并唤醒 DeepSeek；或配置 API Key 后后台自动改代码。",
                                    badge: "国内推理强模型",
                                    color: holderColor("deepseek"),
                                    isCurrentHolder: proj.holder == "deepseek",
                                    canHandOff: canGive,
                                    primaryLabel: "一键带此对话去 DeepSeek",
                                    primaryAction: {
                                        store.runRelayCommand(
                                            title: "交给 DeepSeek (\(proj.name))",
                                            args: ["open", "--project", proj.code, "--tool", "deepseek"] + srcArgs,
                                            triggerDonateAfter: true
                                        )
                                    },
                                    secondaryLabel: "API 自动跑",
                                    secondaryAction: {
                                        store.runRelayCommand(
                                            title: "DeepSeek API 后台接力 (\(proj.name))",
                                            args: ["handoff", "--project", proj.code, "--to", "deepseek"] + srcArgs,
                                            triggerDonateAfter: true
                                        )
                                    }
                                )

                                // 4. OpenAI Codex / Claude Code 互转卡片
                                if proj.homeHolder == "codex" {
                                    ToolCardView(
                                        title: "Claude Code",
                                        subtitle: "当前工程主力为 OpenAI Codex。如果你想把当前 Codex 对话框临时交给 Claude Code 接着改，点这里一键移交。",
                                        badge: "主力为 Codex · 可转交 Claude",
                                        color: holderColor("claude"),
                                        isCurrentHolder: proj.holder == "claude",
                                        canHandOff: canGive,
                                        primaryLabel: "带此对话去 Claude Code 开工",
                                        primaryAction: {
                                            store.runRelayCommand(
                                                title: "交给 Claude Code (\(proj.name))",
                                                args: ["open", "--project", proj.code, "--tool", "claude"] + srcArgs,
                                                triggerDonateAfter: true
                                            )
                                        },
                                        secondaryLabel: "设 Claude 为主力",
                                        secondaryAction: {
                                            store.setBaseHolder("claude")
                                        }
                                    )
                                } else {
                                    ToolCardView(
                                        title: "OpenAI Codex",
                                        subtitle: "支持调用本机 Codex CLI 无人值守分段执行并跑单元测试，也可以点右侧直接把 Codex 设为你的日常主力。",
                                        badge: store.settings.codexModel,
                                        color: holderColor("codex"),
                                        isCurrentHolder: proj.holder == "codex",
                                        canHandOff: canGive,
                                        primaryLabel: "让 Codex 后台自动接着做",
                                        primaryAction: {
                                            store.runRelayCommand(
                                                title: "交给 Codex 自动执行 (\(proj.name))",
                                                args: ["handoff", "--project", proj.code, "--to", "codex"] + srcArgs,
                                                triggerDonateAfter: true
                                            )
                                        },
                                        secondaryLabel: "设 Codex 为主力",
                                        secondaryAction: {
                                            store.setBaseHolder("codex")
                                        }
                                    )
                                }
                            }
                        }

                        // 下一步任务编辑器 (docs/STATE.md)
                        VStack(alignment: .leading, spacing: 8) {
                            HStack {
                                Label("下一步任务清单 (docs/STATE.md) — 接手的 AI 会直接读这里", systemImage: "checklist")
                                    .font(.system(size: 13.5, weight: .bold))
                                Spacer()
                                if store.stateDirty {
                                    Text("有未保存修改")
                                        .font(.system(size: 11))
                                        .foregroundColor(.orange)
                                }
                                Button("保存任务 (STATE.md)") {
                                    store.saveStateMd()
                                }
                                .buttonStyle(.borderedProminent)
                                .controlSize(.small)
                            }

                            TextEditor(text: Binding(
                                get: { store.editedStateMd },
                                set: {
                                    store.editedStateMd = $0
                                    store.stateDirty = true
                                }
                            ))
                            .font(.system(size: 12.5, design: .monospaced))
                            .frame(minHeight: 130, maxHeight: 170)
                            .padding(6)
                            .background(RoundedRectangle(cornerRadius: 8).fill(Color(NSColor.textBackgroundColor)))
                            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.gray.opacity(0.25), lineWidth: 1))
                        }

                        // 快照管理与运行输出面板
                        VStack(alignment: .leading, spacing: 8) {
                            HStack {
                                Label("快照安全箱与实时执行日志", systemImage: "clock.arrow.circlepath")
                                    .font(.system(size: 13.5, weight: .bold))
                                Spacer()
                                Button("立即拍一份快照") {
                                    store.runRelayCommand(title: "手动拍快照 (\(proj.name))", args: ["snapshot", proj.code])
                                }
                                .controlSize(.small)

                                Button("解包最近快照到新文件夹对照") {
                                    store.runRelayCommand(title: "恢复快照到新目录 (\(proj.name))", args: ["restore", proj.code])
                                }
                                .controlSize(.small)
                                .disabled(proj.snapshotsCount == 0)

                                Button("清空日志显示") {
                                    store.consoleOutput = ""
                                }
                                .controlSize(.small)
                            }

                            ScrollView {
                                Text(store.consoleOutput)
                                    .font(.system(size: 11.5, design: .monospaced))
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .textSelection(.enabled)
                                    .padding(8)
                            }
                            .frame(height: 135)
                            .background(RoundedRectangle(cornerRadius: 8).fill(Color.black.opacity(0.86)))
                            .foregroundColor(Color(red: 0.78, green: 0.96, blue: 0.82))
                        }
                    }
                    .padding(18)
                }
            } else {
                VStack(spacing: 14) {
                    Image(systemName: "arrow.triangle.2.circlepath.circle.fill")
                        .font(.system(size: 48))
                        .foregroundColor(.accentColor)
                    Text("还没有添加任何工程")
                        .font(.system(size: 18, weight: .bold))
                    Text("点击下方按钮选择一个你的代码文件夹，即可在 Claude、Codex、Antigravity、豆包工作台、DeepSeek 之间一键接力。")
                        .font(.system(size: 13))
                        .foregroundColor(.secondary)
                        .multilineTextAlignment(.center)
                        .frame(maxWidth: 420)
                    Button("➕ 添加第一个工程文件夹") {
                        store.addProjectInteractive()
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .toolbar {
            ToolbarItemGroup(placement: .automatic) {
                Button(action: { store.refresh(syncEditor: true) }) {
                    Label("刷新状态", systemImage: "arrow.clockwise")
                }
                Button(action: { store.addProjectInteractive() }) {
                    Label("添加工程", systemImage: "folder.badge.plus")
                }
                Button(action: { store.showSettingsSheet = true }) {
                    Label("模型与 API", systemImage: "gearshape")
                }
                Button(action: { store.showDonateSheet = true }) {
                    HStack(spacing: 4) {
                        Image(systemName: "heart.fill")
                            .foregroundColor(.pink)
                        Text("打赏支持")
                            .fontWeight(.semibold)
                    }
                }
            }
        }
        .sheet(isPresented: $store.showDonateSheet) {
            DonateSheetView().environmentObject(store)
        }
        .sheet(isPresented: $store.showSettingsSheet) {
            SettingsSheetView().environmentObject(store)
        }
    }
}

// MARK: - AppDelegate（同时管理独立桌面窗口 + 顶部状态栏快捷入口）

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    let store = RelayStore()
    var mainWindow: NSWindow?
    let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    var timer: Timer?

    func applicationDidFinishLaunching(_ notification: Notification) {
        store.onStateUpdated = { [weak self] in
            self?.updateStatusTitle()
        }
        setupMainWindow()
        setupStatusBar()
        store.refresh(syncEditor: true)
        timer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in
            guard let self = self, !self.store.isBusy else { return }
            self.store.refresh(syncEditor: false)
        }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showMainWindow()
        return true
    }

    func setupMainWindow() {
        if let logo = heliuLogoImage() {
            NSApp.applicationIconImage = logo
        }
        let contentView = MainWindowView().environmentObject(store)
        let win = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1040, height: 760),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        win.center()
        win.title = "合流 AI 接力台 (heliuai.com · OpenAI Codex ⇄ Gemini ⇄ 豆包工作台 ⇄ DeepSeek ⇄ Claude)"
        win.contentView = NSHostingView(rootView: contentView)
        win.isReleasedWhenClosed = false
        win.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        self.mainWindow = win
    }

    @objc func showMainWindow() {
        if mainWindow == nil {
            setupMainWindow()
        } else {
            mainWindow?.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
        }
    }

    @objc func openDonateFromMenu() {
        showMainWindow()
        store.showDonateSheet = true
    }

    func setupStatusBar() {
        let menu = NSMenu()
        menu.delegate = self
        menu.autoenablesItems = false
        statusItem.menu = menu
        updateStatusTitle()
    }

    func updateStatusTitle() {
        var title = "⚡ 合流接力"
        if !store.liveEnabled {
            title = "⚡ 合流·关"
        } else {
            let away = store.projects.filter { $0.holder != $0.homeHolder }
            if !away.isEmpty {
                title = "⚡ 合流 ▶ " + holderDisplayName(away[0].holder)
            }
        }
        statusItem.button?.title = title
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        let openItem = NSMenuItem(title: "🖥 打开【合流 AI 接力台】主窗口…", action: #selector(showMainWindow), keyEquivalent: "o")
        openItem.target = self
        menu.addItem(openItem)

        let donateItem = NSMenuItem(title: "❤️ 觉得好用？任意金额打赏支持…", action: #selector(openDonateFromMenu), keyEquivalent: "")
        donateItem.target = self
        menu.addItem(donateItem)

        menu.addItem(.separator())

        for p in store.projects {
            let info = NSMenuItem(title: "· \(p.name)：在 \(holderDisplayName(p.holder)) 手里（主力：\(holderDisplayName(p.homeHolder))）", action: nil, keyEquivalent: "")
            info.isEnabled = false
            menu.addItem(info)
        }

        menu.addItem(.separator())
        let quitItem = NSMenuItem(title: "退出合流 AI 接力台", action: #selector(quitApp), keyEquivalent: "q")
        quitItem.target = self
        menu.addItem(quitItem)
    }

    @objc func quitApp() {
        NSApp.terminate(nil)
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
