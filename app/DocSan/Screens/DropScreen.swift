import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct DropScreen: View {
    @Bindable var model: AppModel
    @State private var targeted = false
    @State private var creatingProfile = false

    var body: some View {
        VStack(alignment: .leading, spacing: Space.xl) {
            VStack(alignment: .leading, spacing: Space.xs) {
                Text("Sanitize documents")
                    .font(Typeface.screenTitle)
                    .kerning(-0.4)
                Text("Word files, PDFs and PNG or JPEG images. Everything is processed on this Mac.")
                    .font(.system(size: 14))
                    .foregroundStyle(.secondary)
            }

            if model.engineMissing {
                Card {
                    HStack(spacing: Space.m) {
                        IconTile(symbol: "exclamationmark.triangle", tint: .orange)
                        VStack(alignment: .leading, spacing: Space.xxs) {
                            Text("Processing engine missing").font(Typeface.rowTitle)
                            Text("This build does not include the engine. Run app/scripts/bundle-engine.sh, then build again.")
                                .font(Typeface.meta)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .padding(Space.m)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }

            DropZone(targeted: targeted, ready: model.profile != nil, busy: model.busy, choose: chooseFiles)
                .dropDestination(for: URL.self) { urls, _ in
                    guard model.profile != nil, model.busy == nil else { return false }
                    Task { await model.addFiles(urls) }
                    return true
                } isTargeted: { targeted = $0 && model.profile != nil }

            if model.batchLoaded {
                Card {
                    HStack(spacing: Space.m) {
                        IconTile(symbol: "tray.full")
                        VStack(alignment: .leading, spacing: Space.xxs) {
                            Text("Current batch").font(Typeface.rowTitle)
                            Text(model.queue.isEmpty
                                 ? "\(model.items.count) files · review complete"
                                 : "\(model.items.count) files · \(model.queue.count) decisions left")
                                .font(Typeface.meta)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button(model.canOpen(.export) ? "Go to Export" : "Continue Review") {
                            model.open(model.canOpen(.export) ? .export : .review)
                        }
                        .buttonStyle(PrimaryButtonStyle())
                    }
                    .padding(Space.m)
                }
                .transition(.opacity)
            }

            ProfileCard(model: model, creating: $creatingProfile)
        }
        .frame(width: 560)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(nsColor: .windowBackgroundColor))
        .sheet(isPresented: $creatingProfile) {
            NewProfileSheet(model: model)
        }
    }

    private func chooseFiles() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = true
        panel.allowedContentTypes = [.item]
        panel.allowsOtherFileTypes = true
        panel.allowsMultipleSelection = true
        panel.prompt = "Add"
        panel.message = "Choose Word files, PDFs, images or a folder. They are copied to private staging; originals stay untouched."
        guard let window = NSApp.keyWindow ?? NSApp.mainWindow
                ?? NSApp.windows.first(where: { $0.isVisible && $0.canBecomeMain }) else { return }
        NSApp.activate()
        window.makeKeyAndOrderFront(nil)
        panel.beginSheetModal(for: window) { response in
            guard response == .OK else { return }
            let urls = panel.urls
            Task { await model.addFiles(urls) }
        }
    }
}

private struct DropZone: View {
    let targeted: Bool
    let ready: Bool
    let busy: String?
    let choose: () -> Void

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: 18, style: .continuous)
        VStack(spacing: Space.s) {
            if let busy {
                ProgressView().controlSize(.large).padding(.bottom, Space.s)
                Text("\(busy)…").font(.system(size: 16, weight: .semibold))
                Text("Reading, sanitizing and verifying each file on this Mac.")
                    .font(Typeface.body)
                    .foregroundStyle(.secondary)
            } else {
                Image(systemName: "arrow.down.document")
                    .font(.system(size: 34, weight: .light))
                    .symbolRenderingMode(.hierarchical)
                    .foregroundStyle(targeted ? .primary : .secondary)
                    .symbolEffect(.bounce, value: targeted)
                    .padding(.bottom, Space.s)
                Text(ready ? "Drop files or a folder" : "Set up a company profile first")
                    .font(.system(size: 16, weight: .semibold))
                Text(ready ? "Files are copied to private staging. Originals stay untouched."
                           : "The profile lists the names to remove. It stays on this Mac.")
                    .font(Typeface.body)
                    .foregroundStyle(.secondary)
                    .padding(.bottom, Space.m)
                Button("Choose Files…", action: choose)
                    .buttonStyle(PrimaryButtonStyle())
                    .keyboardShortcut("o", modifiers: .command)
                    .disabled(!ready)
            }
        }
        .frame(maxWidth: .infinity)
        .frame(height: 248)
        .background(shape.fill(Color.primary.opacity(targeted ? 0.075 : 0.035)))
        .overlay(
            shape
                .strokeBorder(style: StrokeStyle(lineWidth: 1.5, dash: [6, 5]))
                .foregroundStyle(Color.primary.opacity(targeted ? 0.55 : 0.22))
        )
        .scaleEffect(targeted ? 1.01 : 1)
        .animation(.spring(duration: 0.3, bounce: 0.25), value: targeted)
        .animation(.smooth(duration: 0.25), value: busy)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(ready ? "Drop files or a folder" : "Set up a company profile first")
    }
}

private struct ProfileCard: View {
    @Bindable var model: AppModel
    @Binding var creating: Bool

    var body: some View {
        Card {
            HStack(spacing: Space.m) {
                IconTile(symbol: "building.2")
                VStack(alignment: .leading, spacing: Space.xxs) {
                    if let profile = model.profile {
                        Text(profile.name).font(Typeface.rowTitle)
                        Text("Company profile · \(profile.terms) terms · policy \(profile.policy)")
                            .font(Typeface.meta)
                            .foregroundStyle(.secondary)
                    } else {
                        Text("No company profile").font(Typeface.rowTitle)
                        Text("Import a profile file or create one from the company's names.")
                            .font(Typeface.meta)
                            .foregroundStyle(.secondary)
                    }
                }
                Spacer()
                Menu(model.profile == nil ? "Set Up…" : "Change…") {
                    Button("New Profile…") { creating = true }
                    Button("Import Profile File…", action: importProfile)
                }
                .menuStyle(.button)
                .buttonStyle(SecondaryButtonStyle())
                .fixedSize()
            }
            .padding(Space.m)
        }
    }

    private func importProfile() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.json]
        panel.prompt = "Import"
        panel.message = "Choose a DocSan profile (JSON). A private copy is kept inside the app."
        guard let window = NSApp.keyWindow ?? NSApp.mainWindow
                ?? NSApp.windows.first(where: { $0.isVisible && $0.canBecomeMain }) else { return }
        NSApp.activate()
        window.makeKeyAndOrderFront(nil)
        panel.beginSheetModal(for: window) { response in
            guard response == .OK, let url = panel.url else { return }
            Task { await model.importProfile(url) }
        }
    }
}

private struct NewProfileSheet: View {
    @Bindable var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var aliases = ""
    @State private var people = ""

    var body: some View {
        VStack(alignment: .leading, spacing: Space.xl) {
            VStack(alignment: .leading, spacing: Space.xs) {
                Text("New company profile").font(Typeface.sectionTitle)
                Text("DocSan removes these names wherever they appear. The profile stays on this Mac.")
                    .font(Typeface.body)
                    .foregroundStyle(.secondary)
            }
            Form {
                TextField("Company name", text: $name, prompt: Text("Granola Group"))
                TextField("Other names", text: $aliases, prompt: Text("Granola, GRN, Granola Labs"))
                TextField("People", text: $people, prompt: Text("Jane Doe, John Smith"))
            }
            .formStyle(.grouped)
            Text("Separate names with commas. Short all-capital names such as GRN only match in capitals.")
                .font(Typeface.meta)
                .foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                    .buttonStyle(SecondaryButtonStyle(large: true))
                    .keyboardShortcut(.cancelAction)
                Button("Create Profile") {
                    let split = { (s: String) in s.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty } }
                    Task {
                        await model.createProfile(name: name.trimmingCharacters(in: .whitespaces),
                                                  aliases: split(aliases), people: split(people))
                        dismiss()
                    }
                }
                .buttonStyle(PrimaryButtonStyle(large: true))
                .keyboardShortcut(.defaultAction)
                .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(Space.xxl)
        .frame(width: 520)
    }
}
