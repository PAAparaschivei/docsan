import AppKit
import SwiftUI

/// End-to-end check inside the sandbox, without clicking: `--selftest` runs the
/// real engine on files placed in the app's temporary folder and prints a JSON
/// result. With `--snapshots` it also captures every screen in light and dark.
///
/// Inputs (inside the app container's tmp folder):
///   selftest/profile.json, selftest/in/*, optional selftest/decisions.json
///   mapping words, or "group:names", "group:codes" and "group:other", to
///   "safe", "system" or "company:LABEL"; "find" and "find_as" search a
///   phrase and replace it through the profile. Every image's
///   proposed masks are confirmed as they are; where the verifier still
///   reads a reference, those areas are masked and confirmed again, as the
///   owner's Mask These Areas does.
@MainActor
enum SelfTest {
    static var requested: Bool { CommandLine.arguments.contains("--selftest") }

    static func run(model: AppModel) async {
        let arguments = CommandLine.arguments
        let inputIndex = arguments.firstIndex(of: "--selftest-inputs")
        let base: URL
        if let inputIndex, inputIndex + 1 < arguments.count {
            base = URL(fileURLWithPath: arguments[inputIndex + 1], isDirectory: true)
        } else {
            base = FileManager.default.temporaryDirectory.appendingPathComponent("selftest", isDirectory: true)
        }
        let shots = base.appendingPathComponent("snapshots", isDirectory: true)
        let snapshots = CommandLine.arguments.contains("--snapshots")
        try? FileManager.default.removeItem(at: shots)
        try? FileManager.default.createDirectory(at: shots, withIntermediateDirectories: true)
        var log: [String: Any] = ["engine": model.engine != nil]
        let window = await mainWindow()
        window?.setFrame(NSRect(x: 80, y: 80, width: 1280, height: 820), display: true)

        func shoot(_ name: String) async {
            guard snapshots, let window else { return }
            for (suffix, appearance) in [("light", NSAppearance.Name.aqua), ("dark", .darkAqua)] {
                NSApp.appearance = NSAppearance(named: appearance)
                try? await Task.sleep(for: .milliseconds(800))
                capture(window, to: shots.appendingPathComponent("\(name)-\(suffix).png"))
            }
        }

        if let storage = model.storage, storage.root.lastPathComponent != "DocSan-selftest" {
            FileHandle.standardError.write(Data("refusing to run the self-test on user storage\n".utf8))
            return
        }
        model.reset()
        model.visuals = CommandLine.arguments.contains("--remove-images") ? .labelAll : .keep
        await shoot("1-drop-empty")
        await model.importProfile(base.appendingPathComponent("profile.json"))
        log["profile"] = model.profile?.name
        let inputs = (try? FileManager.default.contentsOfDirectory(at: base.appendingPathComponent("in"),
                                                                    includingPropertiesForKeys: nil)) ?? []
        await model.addFiles(inputs.sorted { $0.lastPathComponent < $1.lastPathComponent })
        log["analyzed"] = model.items.map { [$0.name, $0.file.status] }
        log["decisions"] = model.queue.map(\.word)
        if model.queue.isEmpty, let next = model.awaitingPictures.first {
            log["zero_word_import_opens_pending_picture"] = model.currentPicture?.key == next.picture.key
        }
        await shoot("2-review")
        // The pages the review screen shows, laid out by the text system inside the sandbox.
        if snapshots {
            for item in model.items {
                guard let path = item.file.pages else { continue }
                if let image = pagesSnapshot(URL(fileURLWithPath: path)),
                   let tiff = image.tiffRepresentation, let png = NSBitmapImageRep(data: tiff)?.representation(using: .png, properties: [:]) {
                    try? png.write(to: shots.appendingPathComponent("2c-pages-\(item.name).png"))
                    log["pages_rendered"] = ((log["pages_rendered"] as? Int) ?? 0) + 1
                }
            }
        }
        // A PDF's pages, as the review shows them before export (D-024).
        if let pdf = model.items.first(where: \.isPDF) {
            log["pdf_pages"] = pdf.pictures.map { [$0.title, $0.image.textLayer ?? false, $0.image.masks.count] as [Any] }
            model.show(nil, in: pdf)
            model.previewLayout = .pages
            await shoot("2d-pdf-pages")
            model.previewLayout = .text
            await shoot("2e-pdf-text")
            model.previewLayout = .pages
        }
        if snapshots {
            let image = ImageRenderer(content: SidebarRowsPreview(items: model.items).environment(\.colorScheme, .light))
            image.scale = 2
            if let cg = image.cgImage {
                try? NSBitmapImageRep(cgImage: cg).representation(using: .png, properties: [:])?
                    .write(to: shots.appendingPathComponent("0-sidebar-rows.png"))
            }
        }

        let decisions = (try? JSONSerialization.jsonObject(with: Data(contentsOf: base.appendingPathComponent("decisions.json")))) as? [String: String] ?? [:]
        // "group:names" (or codes, other) marks a whole group, as Redact all does.
        for group in WordDecision.Group.allCases {
            if let rule = decisions["group:\(group)"], rule.hasPrefix("company:") {
                model.markAll(in: group, .company(String(rule.dropFirst("company:".count))))
            }
        }
        for decision in model.queue {
            let rule = decisions[decision.word] ?? "safe"
            if rule == "system" {
                model.mark(decision, .system)
            } else if rule.hasPrefix("company:") {
                model.mark(decision, .company(String(rule.dropFirst("company:".count))))
            }
        }
        await shoot("2b-review-marked")
        await model.applyMarks()
        log["after_review"] = model.items.map { [$0.name, $0.file.status] }
        await shoot("3-review-done")
        // A stale keyboard action must not approve a different displayed component.
        let waiting = model.awaitingPictures
        if waiting.count >= 2 {
            model.show(waiting[0].picture, in: waiting[0].item)
            await model.confirm(waiting[1].picture)
            log["stale_confirmation_rejected"] = model.imageChoices[waiting[1].picture.key]?.confirmed == nil
        }
        let approvalStart = Date.now
        // Images: show the first picture, then confirm every proposed plan.
        if let first = model.awaitingPictures.first {
            model.show(first.picture, in: first.item)
            await shoot("3b-image-review")
        }
        if let page = model.awaitingPictures.first(where: { $0.picture.isPage }) {
            model.show(page.picture, in: page.item)
            await shoot("3f-pdf-page")
            if snapshots {  // the panel is not captured with the window; render it on its own
                let panel = ImageRenderer(content: ImagePanel(model: model, item: page.item, picture: page.picture)
                    .frame(width: 400).padding(28).background(Color.white).environment(\.colorScheme, .light))
                panel.scale = 2
                if let cg = panel.cgImage {
                    try? NSBitmapImageRep(cgImage: cg).representation(using: .png, properties: [:])?
                        .write(to: shots.appendingPathComponent("3f-panel-page.png"))
                }
            }
        }
        log["image_masks"] = model.items.flatMap { item in item.pictures.map { [item.name, $0.image.masks.count] as [Any] } }
        log["pictures_removed"] = model.items.flatMap { item in item.pictures.compactMap { $0.image.removed.map { [item.name, $0] } } }
        // A picture removed whole: the canvas in the window, the panel rendered on its own.
        if snapshots, let item = model.items.first(where: { $0.pictures.contains(where: \.isRemoved) }),
           let removed = item.pictures.first(where: \.isRemoved) {
            model.show(removed, in: item)
            await shoot("3e-removed")
            for (name, picture) in [("removed", removed), ("masked", item.pictures.first { !$0.isRemoved })] {
                guard let picture else { continue }
                let panel = ImageRenderer(content: ImagePanel(model: model, item: item, picture: picture)
                    .frame(width: 400).padding(28).background(Color.white).environment(\.colorScheme, .light))
                panel.scale = 2
                if let cg = panel.cgImage {
                    try? NSBitmapImageRep(cgImage: cg).representation(using: .png, properties: [:])?
                        .write(to: shots.appendingPathComponent("3e-panel-\(name).png"))
                }
            }
        }
        for _ in 0..<40 {
            guard let next = model.awaitingPictures.first else { break }
            model.show(next.picture, in: next.item)
            await model.confirm(next.picture)
        }
        log["approval_seconds"] = Date.now.timeIntervalSince(approvalStart)
        log["after_images"] = model.items.map { [$0.name, $0.file.status] }
        // Where the verifier still reads a reference, do what the owner's Mask
        // These Areas does, then confirm that picture or page again.
        var maskedFound: [[Any]] = []
        for _ in 0..<3 {
            let found = model.items.flatMap { item in item.pictures.filter { !$0.image.found.isEmpty }.map { (item, $0) } }
            guard !found.isEmpty else { break }
            for (item, picture) in found {
                maskedFound.append([item.name, picture.title, picture.image.found.count])
                await model.maskFound(in: picture)
            }
            for _ in 0..<40 {
                guard let next = model.awaitingPictures.first else { break }
                model.show(next.picture, in: next.item)
            await model.confirm(next.picture)
            }
        }
        log["masked_found"] = maskedFound
        log["after_found"] = model.items.map { [$0.name, $0.file.status] }
        // Find a phrase, show it, then replace it through the profile.
        if let item = model.items.first, let phrase = decisions["find"] {
            model.show(nil, in: item)
            model.previewMode = .original
            model.searchText = phrase
            await model.loadProfileWords()
            if let match = model.currentMatch { model.reveal(match) }
            await shoot("3c-find")
            log["find_before"] = ["matches": model.searchMatches.count, "output": model.searchResults.map(\.output).reduce(0, +),
                                  "profile": model.profileChoice(for: phrase) ?? "none"] as [String: Any]
            await model.decide(phrase, as: decisions["find_as"] ?? "PROJECT")
            model.previewMode = .sanitized
            await shoot("3d-find-replaced")
            log["find_after"] = ["original": model.searchResults.map(\.original).reduce(0, +),
                                 "output": model.searchResults.map(\.output).reduce(0, +),
                                 "profile": model.profileChoice(for: phrase) ?? "none"] as [String: Any]
            model.searchText = ""
        }
        model.open(.drop)
        await shoot("4-drop-with-batch")
        model.open(.export)
        await shoot("5-export-ready")

        let out = base.appendingPathComponent("out", isDirectory: true)
        try? FileManager.default.removeItem(at: out)
        try? FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
        await model.export(to: out)
        log["exported_folder"] = model.exported?.folder.lastPathComponent
        log["exported_files"] = model.exported?.files
        await shoot("6-export-done")
        // A post-export edit invalidates the status and only the changed page's
        // approval. The earlier saved artifact remains a historical export.
        if model.exported != nil, let item = model.items.first(where: { !$0.pictures.isEmpty }),
           let picture = item.pictures.first {
            await model.addMask(CGRect(x: 0, y: 0, width: 4, height: 4), to: picture)
            log["post_export_edit_clears_verified_status"] = model.exported == nil
            log["post_export_edit_requires_review"] = model.awaitingPictures.contains { $0.picture.key == picture.key }
        }
        log["error"] = model.errorMessage
        if let data = try? JSONSerialization.data(withJSONObject: log, options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: base.appendingPathComponent("result.json"))
            FileHandle.standardOutput.write(data)
        }
    }

    private static func mainWindow() async -> NSWindow? {
        for _ in 0..<40 {
            if let w = NSApp.windows.first(where: { $0.isVisible && $0.canBecomeMain }) { return w }
            try? await Task.sleep(for: .milliseconds(100))
        }
        return nil
    }

    /// The macOS 26 glass sidebar draws through layers that no capture API
    /// sees, so its rows are rendered separately.
    private static func capture(_ window: NSWindow, to url: URL) {
        window.display()
        guard let frame = window.contentView?.superview,
              let rep = frame.bitmapImageRepForCachingDisplay(in: frame.bounds) else { return }
        frame.cacheDisplay(in: frame.bounds, to: rep)
        try? rep.representation(using: .png, properties: [:])?.write(to: url)
    }
}
