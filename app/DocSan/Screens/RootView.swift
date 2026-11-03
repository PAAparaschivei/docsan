import SwiftUI

/// One window, three steps. The step tabs and the offline status are always visible.
struct RootView: View {
    @Bindable var model: AppModel
    @State private var showOptions = false

    var body: some View {
        VStack(spacing: 0) {
            ZStack {
                switch model.step {
                case .drop:
                    DropScreen(model: model).transition(.opacity)
                case .review:
                    ReviewScreen(model: model).transition(.opacity)
                case .export:
                    ExportScreen(model: model).transition(.opacity)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            StatusBar(model: model)
        }
        .toolbar {
            ToolbarItem(placement: .principal) {
                StepTabs(model: model)
            }
            ToolbarItem(placement: .primaryAction) {
                Menu {
                    Picker("Appearance", selection: $model.appearance) {
                        ForEach(Appearance.allCases, id: \.self) { option in
                            Label(option.title, systemImage: option.symbol).tag(option)
                        }
                    }
                    .pickerStyle(.inline)
                } label: {
                    Image(systemName: model.appearance.symbol)
                }
                .menuIndicator(.hidden)
                .help("Light or dark appearance")
                .accessibilityLabel("Appearance")
            }
            ToolbarItem(placement: .primaryAction) {
                Button {
                    showOptions.toggle()
                } label: {
                    Image(systemName: "slider.horizontal.3")
                }
                .help("Options")
                .accessibilityLabel("Options")
                .popover(isPresented: $showOptions, arrowEdge: .bottom) {
                    OptionsView(model: model)
                }
            }
        }
        .toolbar(removing: .title)
        .animation(.smooth(duration: 0.35), value: model.step)
        .alert("Something went wrong", isPresented: Binding(
            get: { model.errorMessage != nil },
            set: { if !$0 { model.errorMessage = nil } }
        )) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(model.errorMessage ?? "")
        }
        .sheet(isPresented: $model.showingWords) { ProfileWordsSheet(model: model) }
        .onChange(of: model.showingWords) { _, showing in if showing { showOptions = false } }
        .task {
            if !SelfTest.requested { model.appearance.apply() }
            await model.loadProfile()
        }
    }
}

/// Clickable step tabs. A step opens only when it is ready: Review after files
/// are added, Export after every decision is made.
struct StepTabs: View {
    @Bindable var model: AppModel

    var body: some View {
        HStack(spacing: Space.xxs) {
            ForEach(Step.allCases, id: \.self) { s in
                StepTab(
                    step: s,
                    current: model.step == s,
                    done: s.rawValue < model.step.rawValue,
                    enabled: model.canOpen(s)
                ) { model.open(s) }
                .keyboardShortcut(KeyEquivalent(Character("\(s.rawValue + 1)")), modifiers: .command)
            }
        }
        .padding(Space.xxs)
        .background(Capsule().fill(Color.primary.opacity(0.06)))
        .fixedSize()
    }
}

private struct StepTab: View {
    let step: Step
    let current: Bool
    let done: Bool
    let enabled: Bool
    let action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 5) {
                if done {
                    Image(systemName: "checkmark")
                        .font(.system(size: 9, weight: .bold))
                        .foregroundStyle(.secondary)
                }
                Text(step.title)
                    .font(.system(size: 12.5, weight: current ? .semibold : .regular))
                    .foregroundStyle(current ? .primary : .secondary)
            }
            .padding(.horizontal, 14)
            .frame(height: 24)
            .background {
                if current {
                    Capsule()
                        .fill(Color(nsColor: .controlBackgroundColor))
                        .shadow(color: .black.opacity(0.14), radius: 1.5, y: 0.5)
                } else if hovering && enabled {
                    Capsule().fill(Color.primary.opacity(0.06))
                }
            }
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .opacity(enabled ? 1 : 0.45)
        .onHover { hovering = $0 }
        .help(help)
        .accessibilityLabel("\(step.title), step \(step.rawValue + 1) of 3")
        .accessibilityAddTraits(current ? .isSelected : [])
    }

    private var help: String {
        if enabled { return "\(step.title) (⌘\(step.rawValue + 1))" }
        return step == .review ? "Add files first" : "Finish the review first"
    }
}

struct StatusBar: View {
    let model: AppModel

    var body: some View {
        VStack(spacing: 0) {
            Divider()
            HStack {
                Label("Offline · network access blocked", systemImage: "lock.fill")
                    .labelStyle(StatusLabelStyle())
                Spacer()
                if let busy = model.busy {
                    HStack(spacing: 6) {
                        ProgressView().controlSize(.mini)
                        Text("\(busy)…")
                    }
                } else if let profile = model.profile {
                    Text("\(profile.name) · Policy \(profile.policy)\(profile.redactSystemNames ? " · system names redacted" : "")")
                } else {
                    Text("No company profile")
                }
            }
            .font(.system(size: 11.5))
            .foregroundStyle(.secondary)
            .padding(.horizontal, Space.l)
            .frame(height: 30)
        }
        .background(.bar)
    }
}

private struct StatusLabelStyle: LabelStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: 5) {
            configuration.icon.font(.system(size: 9.5, weight: .semibold))
            configuration.title
        }
    }
}

struct OptionsView: View {
    @Bindable var model: AppModel

    var body: some View {
        Form {
            Section {
                Toggle(isOn: Binding(
                    get: { model.redactSystemNames },
                    set: { value in Task { await model.setRedactSystemNames(value) } }
                )) {
                    Text("Redact system names")
                    Text("Company names inside a system name are always redacted: MyAurelan becomes My[CLIENT].")
                }
                Picker(selection: Binding(
                    get: { model.personStyle },
                    set: { value in Task { await model.setPersonStyle(value) } }
                )) {
                    Text("First name and initial").tag("first_name_initial")
                    Text("Numbered labels").tag("label")
                } label: {
                    Text("People's names")
                    Text(model.personStyle == "label"
                         ? "Paul Andrei Aparaschivei becomes [PERSON-1]."
                         : "Paul Andrei Aparaschivei becomes Paul A.; a family name on its own becomes its initial.")
                }
                LabeledContent {
                    Button("Manage Words…") { model.showingWords = true }
                } label: {
                    Text("Learned words")
                    Text("Change words the profile keeps or redacts.")
                }
            } header: {
                Text("Policy")
            }
            .disabled(model.profile == nil)
            Section("Pictures and diagrams in Word files") {
                Picker(selection: Binding(
                    get: { model.visuals },
                    set: { value in
                        model.visuals = value
                        Task { await model.analyze() }
                    }
                )) {
                    ForEach(VisualMode.allCases) { Text($0.title).tag($0) }
                } label: {
                    Text("Pictures and diagrams")
                    Text(model.visuals.detail)
                }
                .pickerStyle(.radioGroup)
            }
            Section("Private storage") {
                LabeledContent("Folder") {
                    Text("~/Library/Application Support/DocSan")
                        .font(.system(size: 12, design: .monospaced))
                        .foregroundStyle(.secondary)
                }
                LabeledContent("Dictionaries", value: "macOS spell checker, English and French")
            }
        }
        .formStyle(.grouped)
        .frame(width: 420)
        .fixedSize(horizontal: false, vertical: true)
    }
}
