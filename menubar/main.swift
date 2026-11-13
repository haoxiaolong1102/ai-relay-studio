// 接力菜单栏图标 / Relay menu-bar item。只负责显示菜单和调用现成的 bin/relay，不含任何交接逻辑。
// 编译：menubar/build.sh    撤掉：菜单里“退出接力图标”，再删掉编出来的 Relay.app 即可。
import Cocoa

// 接力文件夹的位置：环境变量 RELAY_ROOT > 编译时写进 Info.plist 的 RelayRoot（build.sh 写）
let ROOT = ProcessInfo.processInfo.environment["RELAY_ROOT"]
    ?? (Bundle.main.object(forInfoDictionaryKey: "RelayRoot") as? String) ?? ""
// 菜单文字跟系统语言走：中文系统显示中文，其余显示英文。RELAY_LANG=zh|en 可强制。
let ZH: Bool = {
    if let f = ProcessInfo.processInfo.environment["RELAY_LANG"] { return f.hasPrefix("zh") }
    return (Locale.preferredLanguages.first ?? "en").hasPrefix("zh")
}()
func L(_ zh: String, _ en: String) -> String { return ZH ? zh : en }
let PY = "/usr/bin/python3"
let RELAY = ROOT + "/bin/relay"
let HELPER = ROOT + "/menubar/relay_state.py"

struct Proj {
    let code: String, name: String, holder: String, since: String, running: Bool
}

struct State {
    var diskOK = false
    var error: String? = nil
    var live = false
    var projects: [Proj] = []
    var diffs: [String] = []
}

func run(_ args: [String], timeout: TimeInterval = 15) -> (Int32, String) {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: args[0])
    p.arguments = Array(args.dropFirst())
    let pipe = Pipe()
    p.standardOutput = pipe
    p.standardError = pipe
    do { try p.run() } catch { return (-1, "\(error.localizedDescription)") }
    let killer = DispatchWorkItem { if p.isRunning { p.terminate() } }
    DispatchQueue.global().asyncAfter(deadline: .now() + timeout, execute: killer)
    let data = pipe.fileHandleForReading.readDataToEndOfFile()
    p.waitUntilExit()
    killer.cancel()
    return (p.terminationStatus, String(data: data, encoding: .utf8) ?? "")
}

func parseState(_ out: String) -> State {
    var s = State()
    s.diskOK = true
    guard let line = out.split(separator: "\n").last,
          let obj = try? JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any] else {
        s.error = L("读不到接力状态。", "Cannot read relay state. ") + String(out.suffix(200))
        return s
    }
    if (obj["ok"] as? Bool) != true {
        s.error = (obj["error"] as? String) ?? L("读不到接力状态。", "Cannot read relay state.")
        return s
    }
    s.live = (obj["live"] as? Bool) ?? false
    s.diffs = (obj["diffs"] as? [String]) ?? []
    for p in (obj["projects"] as? [[String: Any]]) ?? [] {
        s.projects.append(Proj(code: p["code"] as? String ?? "?", name: p["name"] as? String ?? "?",
                               holder: p["holder"] as? String ?? "unknown", since: p["since"] as? String ?? "",
                               running: p["running"] as? Bool ?? false))
    }
    return s
}

func loadState() -> State {
    guard FileManager.default.fileExists(atPath: RELAY) else { return State() }
    return parseState(run([PY, HELPER, "json"]).1)
}

func holderName(_ h: String) -> String {
    switch h {
    case "claude": return "Claude"
    case "codex": return "Codex"
    case "antigravity": return "Antigravity"
    case "unknown": return L("状态不明", "unknown")
    default: return h
    }
}

func busyLabel(_ p: Proj) -> String {
    let why = p.running ? L("正在跑", "running")
        : p.holder == "claude" ? L("总开关关着", "master switch off")
        : L("已在 \(holderName(p.holder)) 手里", "held by \(holderName(p.holder))")
    return L("\(p.name)（\(why)）", "\(p.name) (\(why))")
}

final class App: NSObject, NSApplicationDelegate, NSMenuDelegate {
    let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    var state = State()
    var timer: Timer?

    func applicationDidFinishLaunching(_ n: Notification) {
        let menu = NSMenu()
        menu.delegate = self
        menu.autoenablesItems = false
        item.menu = menu
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { [weak self] _ in self?.refreshAsync() }
    }

    func refreshAsync() {
        DispatchQueue.global().async {
            let s = loadState()
            DispatchQueue.main.async { self.state = s; self.updateTitle() }
        }
    }

    func refresh() {
        state = loadState()
        updateTitle()
    }

    func updateTitle() {
        let base = L("接力", "Relay")
        var t = base
        if !state.diskOK { t = base + L(" ⚠︎盘没插", " ⚠︎ not found") }
        else if state.error != nil { t = base + " ⚠︎" }
        else if !state.live { t = base + L("·关", " · off") }
        else {
            let away = state.projects.filter { $0.holder != "claude" }
            if !away.isEmpty {
                t = base + " ▶ " + holderName(away[0].holder) + (away.count > 1 ? "×\(away.count)" : "")
            }
        }
        item.button?.title = t
    }

    // 每次点开菜单都现读一遍状态，现搭菜单
    func menuNeedsUpdate(_ menu: NSMenu) {
        refresh()
        menu.removeAllItems()
        if !state.diskOK {
            addInfo(menu, L("没找到接力程序所在的盘", "Relay folder not found"))
            addInfo(menu, L("接力程序在那块盘上，请先插上再点开这里。", "Plug in the drive that holds it, then open this menu again."))
            menu.addItem(.separator())
            if Bundle.main.path(forResource: "switch-offline", ofType: "command") != nil {
                add(menu, L("Claude 被拦着干不了活？临时关掉拦截…", "Claude blocked? Temporarily turn the guard off…"), #selector(offlineSwitch))
            }
        } else if let e = state.error {
            addInfo(menu, L("接力程序出了点问题：", "The relay hit a problem:"))
            addInfo(menu, String(e.prefix(80)))
            add(menu, L("打开总开关菜单（修复、看日志）…", "Open the switch menu (repair, logs)…"), #selector(openSwitch))
        } else {
            addInfo(menu, L("总开关：", "Master switch: ") + (state.live ? L("开", "on") : L("关（接力不干活）", "off (relay inactive)")))
            for p in state.projects {
                addInfo(menu, L("\(p.name)：在 \(holderName(p.holder)) 手里", "\(p.name): held by \(holderName(p.holder))") + (p.running ? L("（正在跑）", " (running)") : ""))
            }
            if !state.diffs.isEmpty { addInfo(menu, L("⚠︎ 锁和抄本不一致，请到“更多”里点修复", "⚠︎ Lock and its copy disagree — use More… → repair")) }
            menu.addItem(.separator())

            add(menu, state.live ? L("关掉总开关", "Turn master switch off") : L("打开总开关", "Turn master switch on"), #selector(toggleLive))
            menu.addItem(.separator())

            let give = NSMenu()
            give.autoenablesItems = false
            for p in state.projects {
                let ok = p.holder == "claude" && !p.running && state.live
                let mi = add(give, ok ? p.name : busyLabel(p),
                             #selector(handoff(_:)), enabled: ok)
                mi.representedObject = p.code
            }
            if state.projects.isEmpty { addInfo(give, L("还没有登记任何工程", "No projects registered yet")) }
            let giveItem = NSMenuItem(title: L("交给 Codex（它自己接着做）", "Hand off to Codex (runs unattended)"), action: nil, keyEquivalent: "")
            giveItem.submenu = give
            menu.addItem(giveItem)

            let agyMenu = NSMenu()
            agyMenu.autoenablesItems = false
            for p in state.projects {
                let ok = p.holder == "claude" && !p.running && state.live
                let mi = add(agyMenu, ok ? p.name : busyLabel(p),
                             #selector(handoffAgy(_:)), enabled: ok)
                mi.representedObject = p.code
            }
            if state.projects.isEmpty { addInfo(agyMenu, L("还没有登记任何工程", "No projects registered yet")) }
            let agyItem = NSMenuItem(title: L("交给 Antigravity（它自己接着做）", "Hand off to Antigravity (runs unattended)"), action: nil, keyEquivalent: "")
            agyItem.submenu = agyMenu
            menu.addItem(agyItem)

            let openMenu = NSMenu()
            openMenu.autoenablesItems = false
            for p in state.projects {
                let ok = p.holder == "claude" && !p.running && state.live
                let mi = add(openMenu, ok ? p.name : busyLabel(p),
                             #selector(openSession(_:)), enabled: ok)
                mi.representedObject = p.code
            }
            if state.projects.isEmpty { addInfo(openMenu, L("还没有登记任何工程", "No projects registered yet")) }
            let openItem = NSMenuItem(title: L("到 Antigravity 开工（拍快照，我自己去做）", "Work in Antigravity myself (snapshot only)"), action: nil, keyEquivalent: "")
            openItem.submenu = openMenu
            menu.addItem(openItem)

            let back = NSMenu()
            back.autoenablesItems = false
            let away = state.projects.filter { $0.holder != "claude" }
            for p in away {
                let mi = add(back, L("\(p.name)（现在在 \(holderName(p.holder)) 手里）", "\(p.name) (held by \(holderName(p.holder)))"), #selector(takeback(_:)))
                mi.representedObject = p.code
            }
            if away.isEmpty { addInfo(back, L("现在所有工程都在 Claude 手里", "Claude holds every project right now")) }
            let backItem = NSMenuItem(title: L("收工 / 交回 Claude", "Done / Give back to Claude"), action: nil, keyEquivalent: "")
            backItem.submenu = back
            menu.addItem(backItem)

            menu.addItem(.separator())
            add(menu, L("看详细状态…", "Detailed status…"), #selector(showStatus))
            add(menu, L("更多（修复、看日志、撤回只读）…", "More (repair, logs, undo read-only)…"), #selector(openSwitch))
        }
        menu.addItem(.separator())
        add(menu, L("退出接力图标", "Quit Relay menu"), #selector(quit))
    }

    @discardableResult
    func add(_ menu: NSMenu, _ title: String, _ sel: Selector, enabled: Bool = true) -> NSMenuItem {
        let mi = NSMenuItem(title: title, action: sel, keyEquivalent: "")
        mi.target = self
        mi.isEnabled = enabled
        menu.addItem(mi)
        return mi
    }

    func addInfo(_ menu: NSMenu, _ title: String) {
        let mi = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        mi.isEnabled = false
        menu.addItem(mi)
    }

    func alert(_ text: String) {
        NSApp.activate(ignoringOtherApps: true)
        let a = NSAlert()
        a.messageText = L("AI 接力", "AI Relay")
        a.informativeText = text
        a.addButton(withTitle: L("好", "OK"))
        a.runModal()
    }

    // 在“终端”窗口里跑一条 relay 命令，用户能看到进度。做法：写一个临时 .command 文件再打开它。
    func runInTerminal(title: String, relayArgs: [String]) {
        guard FileManager.default.fileExists(atPath: RELAY) else {
            alert(L("没找到接力程序所在的盘，这次什么都没做。请先插上那块盘。", "Relay folder not found; nothing was done. Plug in its drive first."))
            return
        }
        let dir = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("relay-menubar")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let file = dir.appendingPathComponent(title + ".command")
        let quoted = relayArgs.map { "'" + $0.replacingOccurrences(of: "'", with: "'\\''") + "'" }.joined(separator: " ")
        let script = """
        #!/bin/sh
        clear
        echo "=== \(title) ==="
        \(PY) '\(RELAY)' \(quoted)
        echo
        echo "\(L("（做完了，这个窗口可以关掉。）", "(Done. You can close this window.)"))"

        """
        do {
            try script.write(to: file, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: file.path)
        } catch {
            alert(L("没能准备好要运行的命令：", "Could not prepare the command: ") + error.localizedDescription)
            return
        }
        NSWorkspace.shared.open(file)
    }

    @objc func handoff(_ sender: NSMenuItem) {
        guard let code = sender.representedObject as? String else { return }
        runInTerminal(title: L("交给 Codex", "Hand off to Codex") + " - \(code)", relayArgs: ["handoff", "--project", code])
    }

    @objc func handoffAgy(_ sender: NSMenuItem) {
        guard let code = sender.representedObject as? String else { return }
        runInTerminal(title: L("交给 Antigravity", "Hand off to Antigravity") + " - \(code)", relayArgs: ["handoff", "--project", code, "--to", "antigravity"])
    }

    @objc func openSession(_ sender: NSMenuItem) {
        guard let code = sender.representedObject as? String else { return }
        runInTerminal(title: L("到 Antigravity 开工", "Open in Antigravity") + " - \(code)", relayArgs: ["open", "--project", code, "--tool", "antigravity"])
    }

    @objc func takeback(_ sender: NSMenuItem) {
        guard let code = sender.representedObject as? String else { return }
        runInTerminal(title: L("交回 Claude", "Give back to Claude") + " - \(code)", relayArgs: ["takeback", "--project", code])
    }

    @objc func openSwitch() {
        runInTerminal(title: L("接力总开关", "Relay switch"), relayArgs: ["switch"])
    }

    @objc func toggleLive() {
        let turnOn = !state.live
        if !turnOn {
            NSApp.activate(ignoringOtherApps: true)
            let a = NSAlert()
            a.messageText = L("关掉接力总开关？", "Turn the relay master switch off?")
            a.informativeText = L("关掉后所有接力钩子都不干活：不拦、不记。随时可以从这个菜单再打开。", "While off, all relay hooks do nothing: no guarding, no logging. Turn it back on from this menu any time.")
            a.addButton(withTitle: L("取消", "Cancel"))
            a.addButton(withTitle: L("关掉", "Turn off"))
            if a.runModal() != .alertSecondButtonReturn { return }
        }
        let (rc, out) = run([PY, HELPER, "live", turnOn ? "on" : "off"])
        let s = parseState(out)
        if rc != 0 || s.error != nil {
            alert(L("没改成：", "Change failed: ") + (s.error ?? String(out.suffix(300))))
        }
        refresh()
    }

    @objc func showStatus() {
        let (_, out) = run([PY, RELAY, "status"])
        alert(out.isEmpty ? L("读不到状态。", "Cannot read status.") : out)
    }

    @objc func offlineSwitch() {
        // 盘不在时用的应急办法：直接跑随程序带着的“接力总开关”启动文件副本（它自己会弹窗问）。
        guard let path = Bundle.main.path(forResource: "switch-offline", ofType: "command") else { return }
        DispatchQueue.global().async { _ = run(["/bin/sh", path], timeout: 600) }
    }

    @objc func quit() { NSApp.terminate(nil) }
}

let app = NSApplication.shared
let delegate = App()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
