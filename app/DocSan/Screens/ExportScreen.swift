import AppKit
import SwiftUI

struct ExportScreen: View {
    @Bindable var model: AppModel

    private var ready: [BatchItem] { model.items.filter { $0.state == .ready } }
    private var blocked: [BatchItem] { model.items.filter { $0.state != .ready } }

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                content
                    .frame(width: 640)
                    .padding(.top, Space.xxxl)
                    .padding(.bottom, Space.xl)
                    .frame(maxWidth: .infinity)
            }
            actions
        }
        .background(Color(nsColor: .windowBackgroundColor))
        .animation(.smooth(duration: 0.3), value: model.exported)
    }

    private var content: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .center, spacing: Space.l) {
                IconTile(symbol: ready.isEmpty ? "exclamationmark.triangle" : "checkmark.seal.fill",
                         size: 48, tint: ready.isEmpty ? .orange : .green)
                VStack(alignment: .leading, spacing: Space.xxs) {
                    Text(title)
                        .font(.system(size: 24, weight: .bold))
                        .kerning(-0.4)
                        .contentTransition(.opacity)
                    Text(subtitle)
                        .font(Typeface.body)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                Spacer()
                Readout(value: ready.count, unit: "of \(model.items.count) \(model.items.count == 1 ? "file" : "files")")
            }

            ProgressView(value: Double(ready.count), total: Double(max(model.items.count, 1)))
                .progressViewStyle(.linear)
                .tint(.primary)
                .padding(.top, Space.l)
            HStack {
                Text(model.exported == nil ? "\(ready.count) ready for verification" : "\(ready.count) verified")
                Spacer()
                Text("\(blocked.count) not exported")
            }
            .font(Typeface.meta)
            .foregroundStyle(.secondary)
            .padding(.top, Space.s)

            if !ready.isEmpty {
                Card {
                    ForEach(Array(ready.enumerated()), id: \.element.id) { i, item in
                        if i > 0 { InsetDivider() }
                        FileRow(symbol: item.symbol,
                                title: item.file.output ?? item.name,
                                detail: item.file.output == nil ? "Gets a neutral name on export" : "From \(item.name)") {
                            StateLabel(state: .ready, text: model.exported == nil ? "Reviewed" : "Exported")
                        }
                    }
                }
                .padding(.top, Space.xl)
            }

            if imagesBlocked {
                Card {
                    HStack(spacing: Space.m) {
                        IconTile(symbol: "photo.badge.exclamationmark", tint: .orange)
                        VStack(alignment: .leading, spacing: Space.xxs) {
                            Text("Some visuals can't be read").font(Typeface.rowTitle)
                            Text("Replace only those with a label such as [IMAGE-REMOVED]; every other picture and diagram stays, with names masked.")
                                .font(Typeface.meta)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button("Label Those") {
                            model.visuals = .labelUnreadable
                            Task { await model.analyze() }
                        }
                        .buttonStyle(PrimaryButtonStyle())
                        .disabled(model.busy != nil)
                    }
                    .padding(Space.m)
                }
                .padding(.top, Space.xl)
            }

            if !blocked.isEmpty {
                SectionLabel(text: "Not exported")
                    .padding(.top, Space.xl)
                    .padding(.leading, Space.m)
                    .padding(.bottom, Space.s)
                Card {
                    ForEach(Array(blocked.enumerated()), id: \.element.id) { i, item in
                        if i > 0 { InsetDivider() }
                        FileRow(symbol: item.symbol, title: item.name, detail: item.blockReason.capitalizedFirst) {
                            StateLabel(state: .blocked, text: "Blocked")
                        }
                        .help(item.file.reasons.prefix(8).joined(separator: "\n"))
                    }
                }
            }
        }
    }

    /// Pinned below the list so the main action is always visible.
    private var actions: some View {
        HStack(spacing: Space.m) {
            Label("The replacement list exists only while this batch is open.", systemImage: "lock")
                .font(Typeface.meta)
                .foregroundStyle(.secondary)
            Spacer()
            if let exported = model.exported {
                Button("New Batch", action: model.reset)
                    .buttonStyle(SecondaryButtonStyle(large: true))
                Button("Show in Finder") {
                    NSWorkspace.shared.activateFileViewerSelecting([exported.folder])
                }
                .buttonStyle(PrimaryButtonStyle(large: true))
                .keyboardShortcut(.defaultAction)
            } else {
                Button("Export to Folder…", action: chooseFolder)
                    .buttonStyle(PrimaryButtonStyle(large: true))
                    .keyboardShortcut(.defaultAction)
                    .disabled(ready.isEmpty || model.busy != nil)
            }
        }
        .frame(width: 640)
        .padding(.vertical, Space.l)
        .frame(maxWidth: .infinity)
        .overlay(alignment: .top) { Divider() }
    }

    private var imagesBlocked: Bool {
        model.visuals == .keep && blocked.contains { $0.blockedByVisuals }
    }

    private var title: String {
        if model.exported != nil { return "Exported" }
        return ready.isEmpty ? "Nothing to export yet" : "Ready to verify and export"
    }

    private var subtitle: String {
        if let busy = model.busy { return "\(busy)…" }
        if let exported = model.exported {
            return "Saved in “\(exported.folder.lastPathComponent)” in \(exported.folder.deletingLastPathComponent().lastPathComponent)"
        }
        if ready.isEmpty { return "Every file is blocked. The reasons are listed below." }
        return "Final verification runs before files are saved · policy \(model.profile?.policy ?? "")"
    }

    private func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.canCreateDirectories = true
        panel.prompt = "Export Here"
        panel.message = "DocSan creates a new folder here with the verified files and a summary."
        guard let window = NSApp.keyWindow ?? NSApp.mainWindow
                ?? NSApp.windows.first(where: { $0.isVisible && $0.canBecomeMain }) else { return }
        NSApp.activate()
        window.makeKeyAndOrderFront(nil)
        panel.beginSheetModal(for: window) { response in
            guard response == .OK, let url = panel.url else { return }
            Task { await model.export(to: url) }
        }
    }
}

private extension String {
    var capitalizedFirst: String { prefix(1).uppercased() + dropFirst() }
}
