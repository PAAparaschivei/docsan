import SwiftUI

/// Every word the profile has learned, kept or redacted, with the choice to
/// change it. Kept words are grouped like the review, possible names first.
struct ProfileWordsSheet: View {
    @Bindable var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var filter = ""
    @State private var focusedWord: String?
    @State private var changes: [String: String] = [:]  // word -> "safe", "remove" or a label

    private static let labels = ["PERSON", "ORG", "CLIENT", "SITE", "PROJECT", "PRODUCT", "DOMAIN", "ADDRESS", "ID", "SYSTEM"]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: Space.xs) {
                Text("Learned words").font(Typeface.sectionTitle).kerning(-0.3)
                Text("Every word your profile keeps or redacts. Change any of them; the batch is checked again when you save.")
                    .font(Typeface.body)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 5) {
                    Image(systemName: "magnifyingglass").font(.system(size: 11)).foregroundStyle(.secondary)
                    TextField("Filter", text: $filter).textFieldStyle(.plain)
                }
                .padding(.horizontal, 9)
                .frame(height: 28)
                .background(Color.primary.opacity(0.06), in: Capsule())
                .padding(.top, Space.s)
            }
            .padding(Space.xl)

            if let words = model.profileWords {
                List {
                    ForEach(keptGroups(words), id: \.title) { group in
                        Section {
                            ForEach(group.words, id: \.self) { word in row(word, current: "safe") }
                        } header: {
                            HStack {
                                Text("\(group.title) · kept")
                                Spacer()
                                if group.bulk != nil {
                                    Button("Redact all as \(group.bulk!)") {
                                        for w in group.words { changes[w] = group.bulk! }
                                    }
                                    .buttonStyle(.plain)
                                    .font(.system(size: 11.5, weight: .semibold))
                                }
                            }
                        }
                    }
                    let terms = words.terms.filter { matches($0.text) && $0.text != model.profile?.name }
                    if !terms.isEmpty {
                        Section("Redacted or classified · \(terms.count)") {
                            ForEach(terms, id: \.self) { term in row(term.text, current: term.label) }
                        }
                    }
                }
                .listStyle(.inset)
            } else {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            }

            HStack {
                Text(changes.isEmpty ? "No changes" : "\(changes.count) \(changes.count == 1 ? "change" : "changes")")
                    .font(Typeface.meta)
                    .foregroundStyle(.secondary)
                Spacer()
                Button("Cancel") { dismiss() }
                    .buttonStyle(SecondaryButtonStyle())
                    .keyboardShortcut(.cancelAction)
                Button("Save") {
                    let pending = changes
                    dismiss()
                    Task { await model.reclassify(pending) }
                }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(changes.isEmpty)
                .keyboardShortcut(.defaultAction)
            }
            .padding(Space.xl)
            .overlay(alignment: .top) { Divider() }
        }
        .frame(width: 560, height: 640)
        .task { await model.loadProfileWords() }
    }

    private func matches(_ word: String) -> Bool {
        filter.isEmpty || word.range(of: filter, options: [.caseInsensitive, .diacriticInsensitive]) != nil
    }

    private struct KeptGroup {
        let title: String
        let words: [String]
        let bulk: String?
    }

    /// Kept words in the review's groups. Glossary words (months, technical
    /// terms) are last: they no longer need to be kept by the profile.
    private func keptGroups(_ words: ProfileWords) -> [KeptGroup] {
        var groups: [WordDecision.Group: [String]] = [:]
        var glossary: [String] = []
        for kept in words.safe where matches(kept.word) {
            if kept.kind == "glossary" {
                glossary.append(kept.word)
                continue
            }
            let probe = WordDecision(word: kept.word, context: "", files: 0, count: 0, source: "", kind: kept.kind)
            groups[probe.group, default: []].append(kept.word)
        }
        var out = WordDecision.Group.allCases.compactMap { g in
            groups[g].map { KeptGroup(title: g.title, words: $0, bulk: g == .names ? "PERSON" : nil) }
        }
        if !glossary.isEmpty { out.append(KeptGroup(title: "Dictionary and glossary words", words: glossary, bulk: nil)) }
        return out
    }

    private func row(_ word: String, current: String) -> some View {
        let choice = changes[word] ?? current
        return HStack {
            Text(word).font(.system(size: 13, weight: .medium))
            if changes[word] != nil {
                Text("changed").font(Typeface.meta).foregroundStyle(.orange)
            }
            Spacer()
            Menu {
                Button("Keep") { set(word, "safe", current) }
                Button("Keep as system name") { set(word, "SYSTEM", current) }
                Divider()
                Section("Redact as") {
                    ForEach(Self.labels.filter { $0 != "SYSTEM" }, id: \.self) { label in
                        Button(label) { set(word, label, current) }
                    }
                }
                Divider()
                Button("Forget This Word", role: .destructive) { set(word, "remove", current) }
            } label: {
                Text(title(choice))
                    .font(.system(size: 11.5, weight: .semibold))
                    .padding(.horizontal, 9)
                    .frame(height: 22)
                    .foregroundStyle(redacts(choice) ? Color(nsColor: .windowBackgroundColor) : .primary)
                    .background(redacts(choice) ? Color.primary : Color.primary.opacity(0.07), in: Capsule())
            }
            .menuStyle(.button)
            .buttonStyle(.plain)
            .menuIndicator(.hidden)
            .fixedSize()
        }
        .padding(.vertical, 2)
        .background(focusedWord == word ? Color.primary.opacity(0.06) : .clear)
        .contentShape(Rectangle())
        .onTapGesture { focusedWord = word }
        .accessibilityElement(children: .combine)
        .accessibilityAction { focusedWord = word }
        .accessibilityAction(named: Text("Keep word")) { set(word, "safe", current) }
        .accessibilityAction(named: Text("Keep system name")) { set(word, "SYSTEM", current) }
        .accessibilityAction(named: Text("Redact as DOMAIN")) { set(word, "DOMAIN", current) }
        .accessibilityAction(named: Text("Redact as ID")) { set(word, "ID", current) }
    }

    private func set(_ word: String, _ choice: String, _ current: String) {
        changes[word] = choice == current ? nil : choice
    }

    private func redacts(_ choice: String) -> Bool { choice != "safe" && choice != "SYSTEM" && choice != "remove" }

    private func title(_ choice: String) -> String {
        switch choice {
        case "safe": "Keep"
        case "SYSTEM": "System"
        case "remove": "Forget"
        default: "Redact · \(choice)"
        }
    }
}
