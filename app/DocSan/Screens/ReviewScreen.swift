import SwiftUI

struct ReviewScreen: View {
    @Bindable var model: AppModel
    @State private var columns: NavigationSplitViewVisibility = .all

    var body: some View {
        NavigationSplitView(columnVisibility: $columns) {
            List(selection: $model.selectedFile) {
                Section {
                    ForEach(model.items) { item in
                        SidebarFileRow(item: item).tag(item.id)
                    }
                } header: {
                    HStack {
                        Text("Batch")
                        Spacer()
                        Text("\(model.items.count) \(model.items.count == 1 ? "file" : "files")").monospacedDigit()
                    }
                }
            }
            .navigationSplitViewColumnWidth(min: 240, ideal: 264, max: 320)
        } detail: {
            // A fixed panel rather than `.inspector`: on macOS 27 an inspector in the
            // detail column of this split view triggers an Auto Layout loop.
            HStack(spacing: 0) {
                DocumentPreview(model: model, mode: $model.previewMode)
                Divider()
                DecisionPanel(model: model)
                    .frame(width: 380)
                    .background(Color(nsColor: .windowBackgroundColor))
            }
        }
        // Find a word: step through every occurrence, then keep or replace it.
        .searchable(text: $model.searchText, placement: .toolbar, prompt: "Find a word")
        .onSubmit(of: .search) { model.goToMatch(1) }
        .onChange(of: model.searchText) {
            model.matchIndex = 0
            if let match = model.currentMatch { model.reveal(match) }
            if model.profileWords == nil, !model.searchNeedle.isEmpty { Task { await model.loadProfileWords() } }
        }
        .onChange(of: model.previewMode) { model.matchIndex = 0 }
        .background {
            Group {
                Button("Next Match") { model.goToMatch(1) }.keyboardShortcut("g")
                Button("Previous Match") { model.goToMatch(-1) }.keyboardShortcut("g", modifiers: [.command, .shift])
            }
            .hidden()
        }
    }
}

struct SidebarFileRow: View {
    let item: BatchItem

    var body: some View {
        HStack(spacing: Space.m) {
            IconTile(symbol: item.symbol, size: 30)
            VStack(alignment: .leading, spacing: 3) {
                Text(item.name)
                    .font(Typeface.rowTitle)
                    .lineLimit(1)
                    .truncationMode(.middle)
                StateLabel(state: item.state, text: item.detail)
                    .lineLimit(1)
            }
        }
        .padding(.vertical, 5)
    }
}

// MARK: - Document preview

private struct DocumentPreview: View {
    @Bindable var model: AppModel
    @Binding var mode: PreviewMode

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: Space.m) {
                Text(model.selectedItem?.name ?? "").font(Typeface.rowTitle).lineLimit(1).truncationMode(.middle)
                    .layoutPriority(1)
                if let item = model.selectedItem, !item.isImage, !item.pictures.isEmpty {
                    PicturePicker(model: model, item: item)
                }
                Spacer()
                if model.currentPicture == nil, model.selectedItem?.isImage == false {
                    Picker("Layout", selection: $model.previewLayout) {
                        ForEach(PreviewLayout.allCases, id: \.self) { layout in
                            Image(systemName: layout.symbol).help(layout.help).tag(layout)
                        }
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                    .fixedSize()
                }
                Picker("View", selection: $mode) {
                    ForEach(PreviewMode.allCases, id: \.self) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .fixedSize()
            }
            .padding(.horizontal, Space.xxl)
            .padding(.top, Space.l)
            .padding(.bottom, Space.m)
            if model.searchNeedle.count >= 2 {
                FindBar(model: model)
                    .padding(.horizontal, Space.xxl)
                    .padding(.bottom, Space.m)
            }

            if let url = pagesURL {
                // The pages, described by the engine from the same model the output is written from:
                // the original, or the sanitized file with its masks as proposed.
                PagesView(url: url)
            } else {
            ScrollViewReader { proxy in
            ScrollView {
                Group {
                    if let item = model.selectedItem {
                        if let picture = model.currentPicture {
                            ImageCanvas(model: model, picture: picture, mode: mode, search: model.searchText,
                                        currentLine: model.currentMatch.flatMap { $0.picture == picture.key ? $0.line : nil })
                        } else if item.state == .blocked && item.file.previewSanitized.isEmpty {
                            BlockedNotice(item: item)
                        } else if item.isPDF && model.previewLayout == .pages {
                            PDFPages(model: model, item: item, mode: mode)
                        } else {
                            Paper(lines: mode == .sanitized ? item.file.previewSanitized : item.file.previewOriginal,
                                  pending: mode == .sanitized ? Set(model.queue.map(\.id)) : [],
                                  focused: mode == .sanitized ? model.focused : nil,
                                  search: model.searchText,
                                  current: model.currentMatch.flatMap { $0.file == item.id && $0.picture == nil ? ($0.line, $0.occurrence) : nil },
                                  pictures: item.pictures,
                                  open: { model.show($0, in: item) })
                        }
                    }
                }
                .padding(.horizontal, Space.xxl)
                .padding(.top, Space.m)
                .padding(.bottom, Space.xxxl)
                .frame(maxWidth: .infinity)
            }
            .onChange(of: model.currentMatch) { _, match in
                guard let match, match.picture == nil else { return }
                withAnimation(.easeOut(duration: 0.2)) { proxy.scrollTo(match.line, anchor: .center) }
            }
            }
            }
        }
        .background(Color(nsColor: .underPageBackgroundColor))
    }

    /// The pages to show, when the pages layout applies.
    private var pagesURL: URL? {
        guard model.previewLayout == .pages, model.currentPicture == nil, let item = model.selectedItem, !item.isImage else { return nil }
        let path = mode == .original ? item.file.pagesOriginal : item.file.pages
        guard let path, FileManager.default.fileExists(atPath: path) else { return nil }
        return URL(fileURLWithPath: path)
    }
}

/// Switches the preview between the whole document and each of its pictures,
/// or each page of a PDF.
private struct PicturePicker: View {
    @Bindable var model: AppModel
    let item: BatchItem

    private var whole: String {
        let n = item.pictures.count
        return item.isPDF ? "All pages · \(n)" : "Document · \(n) \(n == 1 ? "image" : "images")"
    }

    var body: some View {
        Menu {
            Button(item.isPDF ? "All Pages" : "Document") { model.show(nil, in: item) }
            Divider()
            ForEach(item.pictures) { picture in
                Button {
                    model.show(picture, in: item)
                } label: {
                    Label(picture.title,
                          systemImage: picture.isRemoved ? "minus.circle" : picture.awaiting ? "circle" : "checkmark.circle.fill")
                }
            }
        } label: {
            HStack(spacing: 4) {
                Image(systemName: model.currentPicture == nil ? "doc.text" : item.isPDF ? "doc.richtext" : "photo")
                Text(model.currentPicture.map { "\($0.title) of \(item.pictures.count)" } ?? whole)
                Image(systemName: "chevron.up.chevron.down").font(.system(size: 8, weight: .bold))
            }
            .font(.system(size: 12, weight: .medium))
            .padding(.horizontal, 9)
            .frame(height: 24)
            .background(Color.primary.opacity(0.07), in: Capsule())
        }
        .menuStyle(.button)
        .buttonStyle(.plain)
        .menuIndicator(.hidden)
        .fixedSize()
    }
}

/// Find a word: where it is, how often it survives, and what to do with it.
/// ‹ › (or Return, ⌘G and ⇧⌘G) show each occurrence in turn; Keep and
/// Replace as save the choice in the profile and check the batch again.
private struct FindBar: View {
    @Bindable var model: AppModel
    @State private var confirmKeep = false

    private static let labels = ["PERSON", "ORG", "CLIENT", "PROJECT", "PRODUCT", "SITE", "DOMAIN", "ADDRESS", "ID"]

    var body: some View {
        let word = model.searchNeedle
        let matches = model.searchMatches
        let results = model.searchResults
        let before = results.reduce(0) { $0 + $1.original }
        let left = results.reduce(0) { $0 + $1.output }
        let choice = model.profileChoice(for: word)
        HStack(spacing: Space.s) {
            HStack(spacing: 0) {
                Button { model.goToMatch(-1) } label: { Image(systemName: "chevron.left").frame(width: 24, height: 22) }
                    .help("Previous (⇧⌘G)")
                Button { model.goToMatch(1) } label: { Image(systemName: "chevron.right").frame(width: 24, height: 22) }
                    .help("Next (⌘G or Return)")
            }
            .buttonStyle(.plain)
            .font(.system(size: 11, weight: .bold))
            .background(Color.primary.opacity(0.07), in: Capsule())
            .disabled(matches.isEmpty)
            if matches.isEmpty && model.previewMode == .sanitized && before > 0 {
                Button("Show Original") { model.previewMode = .original }
                    .buttonStyle(.plain)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(Color.accentColor)
                    .fixedSize()
                    .help("It is gone from the output; see where it was")
            } else {
                Text(matches.isEmpty ? "No match" : "\(min(model.matchIndex, matches.count - 1) + 1) of \(matches.count)")
                    .font(.system(size: 12, weight: .semibold))
                    .monospacedDigit()
                    .frame(minWidth: 52, alignment: .leading)
            }
            Image(systemName: left == 0 ? "checkmark.circle.fill" : "exclamationmark.circle.fill")
                .foregroundStyle(before == 0 ? Color.secondary : (left == 0 ? .green : .orange))
            Text(summary(before: before, left: left))
                .font(.system(size: 12))
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .fixedSize()
                .help("Found in the originals; left in the output")
            Spacer(minLength: Space.s)
            Text(status(choice))
                .font(.system(size: 11.5, weight: .medium))
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .fixedSize()
            Button("Keep") {
                if let choice, choice != "safe", choice != "SYSTEM" { confirmKeep = true } else { decide("safe") }
            }
            .buttonStyle(SecondaryButtonStyle(height: 24))
            .fixedSize()
            .disabled(model.busy != nil || choice == "safe")
            Menu {
                ForEach(Self.labels, id: \.self) { label in
                    Button(label) { decide(label) }
                }
                Divider()
                Button("System Name (kept unless system names are redacted)") { decide("SYSTEM") }
            } label: {
                Text("Replace as")
                    .font(.system(size: 12.5, weight: .medium))
                    .padding(.horizontal, 10)
                    .frame(height: 24)
                    .foregroundStyle(Color(nsColor: .windowBackgroundColor))
                    .background(Color.primary, in: RoundedRectangle(cornerRadius: 7, style: .continuous))
            }
            .menuStyle(.button)
            .buttonStyle(.plain)
            .menuIndicator(.hidden)
            .fixedSize()
            .disabled(model.busy != nil)
        }
        .padding(.horizontal, Space.m)
        .padding(.vertical, 7)
        .background(Color(nsColor: .windowBackgroundColor), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .confirmationDialog("Stop redacting “\(word)”?", isPresented: $confirmKeep) {
            Button("Keep “\(word)”", role: .destructive) { decide("safe") }
        } message: {
            Text("Your profile redacts it as \(choice ?? "a term"). Keeping it leaves it readable in every document.")
        }
    }

    private func decide(_ choice: String) {
        let word = model.searchNeedle
        Task { await model.decide(word, as: choice) }
    }

    private func summary(before: Int, left: Int) -> String {
        if before == 0 && left == 0 { return "Not in this batch" }
        if left == 0 { return "\(before) found · all gone" }
        return "\(before) found · \(left) left"
    }

    private func status(_ choice: String?) -> String {
        switch choice {
        case nil: "New word"
        case "safe": "Kept"
        case "SYSTEM": "System name"
        case let label?: "Replaced as \(label)"
        }
    }
}

private struct BlockedNotice: View {
    let item: BatchItem

    var body: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            Label("This file is not exported", systemImage: FileState.blocked.symbol)
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(.primary)
            Text(guidance)
                .font(Typeface.body)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            VStack(alignment: .leading, spacing: Space.xs) {
                ForEach(Array(item.file.reasons.prefix(6).enumerated()), id: \.offset) { _, reason in
                    Text(reason).font(.system(size: 11.5, design: .monospaced)).foregroundStyle(.secondary)
                }
            }
            .padding(.top, Space.xs)
        }
        .padding(Space.xl)
        .frame(maxWidth: 580, alignment: .leading)
        .background(Color(nsColor: .windowBackgroundColor), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
    }

    private var guidance: String {
        switch item.blockReason {
        case "format not supported yet": "This version reads Word files, PDFs and PNG or JPEG images. Older Word formats arrive in a later version: save the file as .docx first."
        case "encrypted PDF": "The PDF is protected. Open it in Preview, export it as a PDF without a password, then add it again."
        case "XFA form": "This is an XFA form, which only Adobe software can draw, so DocSan cannot see all of it. Print it to PDF first, then add that file."
        case "PDF portfolio": "This PDF is a portfolio: its content is in attached files. Save each attached file and add them instead."
        case "redactions never applied": "The PDF marks text for redaction, but the redaction was never applied, so the text is still there. Apply the redactions in your PDF editor first."
        case "content DocSan can't draw": "Part of a page is stored in a way macOS cannot draw, so DocSan cannot check it. Print the file to PDF first, then add that file."
        case "unknown PDF part": "The PDF holds a part DocSan has no rule for, so it cannot account for the whole file. The details below name the part."
        case "PDF cannot be read": "The file is damaged, cut short or not a PDF. Open it in Preview and export it as a PDF again."
        case "too many pages": "The PDF has more than 500 pages. Split it into smaller files, then add them."
        case "image cannot be read": "DocSan reads PNG and JPEG images. This file is damaged, cut short or not an image."
        case "encrypted or legacy file": "Remove the password, or save it as .docx in Word, then add it again."
        case "contains images", "contains charts", "contains SmartArt", "contains embedded objects", "contains equations", "contains drawings",
             "contains vector images", "contains drawings DocSan can't draw":
            "DocSan can't read one of the visuals in this file, so it can't check it for names. In Options, choose Keep them, label what DocSan can't read: that visual becomes a label and every other picture stays, with names masked."
        default: "DocSan only exports what it can fully inspect. The details below say what stopped it."
        }
    }
}

/// A page of the document. Pages stay light in both themes, as in Preview and Pages.
private struct Paper: View {
    let lines: [EngineLine]
    let pending: Set<String>
    let focused: String?
    var search: String = ""
    var current: (line: Int, occurrence: Int)? = nil
    var pictures: [Picture] = []
    var open: (Picture) -> Void = { _ in }

    var body: some View {
        LazyVStack(alignment: .leading, spacing: 12) {
            ForEach(Array(lines.enumerated()), id: \.offset) { index, line in
                if line.kind == "image" || line.kind == "page" {
                    pictureTile(line.level, page: line.kind == "page").id(index)
                } else if !line.text.trimmingCharacters(in: .whitespaces).isEmpty {
                    lineView(line, index: index).id(index)
                }
            }
        }
        .foregroundStyle(Color(white: 0.11))
        .padding(.horizontal, 52)
        .padding(.top, 48)
        .padding(.bottom, 40)
        .frame(maxWidth: 580, alignment: .leading)
        .background(.white, in: RoundedRectangle(cornerRadius: 3))
        .shadow(color: .black.opacity(0.07), radius: 0.5, y: 0)
        .shadow(color: .black.opacity(0.10), radius: 16, y: 6)
        .environment(\.colorScheme, .light)
    }

    /// A picture in the document, or the start of a PDF page: its number, its
    /// state, and a click to open it.
    @ViewBuilder private func pictureTile(_ index: Int, page: Bool = false) -> some View {
        let picture = pictures.first { $0.index == index }
        Button {
            if let picture { open(picture) }
        } label: {
            HStack(spacing: Space.s) {
                Image(systemName: page ? "doc.richtext" : "photo").font(.system(size: 13))
                Text(page ? "Page \(index)" : "Image \(index)").font(.system(size: 12.5, weight: .semibold))
                Text(picture.map { $0.isRemoved ? "removed whole" : $0.awaiting ? "confirm its masks" : "masks confirmed" } ?? "")
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
                Spacer()
                Image(systemName: "chevron.right").font(.system(size: 10, weight: .semibold)).foregroundStyle(.tertiary)
            }
            .padding(.horizontal, Space.m)
            .frame(height: 40)
            .background(Color(white: 0.955), in: RoundedRectangle(cornerRadius: 6))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    @ViewBuilder private func lineView(_ line: EngineLine, index: Int) -> some View {
        let text = Text(styled(line.text, current: current?.line == index ? current?.occurrence : nil))
        switch line.kind {
        case "title":
            text.font(.system(size: 22, weight: .bold)).kerning(-0.3)
        case "heading":
            text.font(.system(size: max(13.5, 19 - CGFloat(max(line.level, 1) - 1) * 1.8), weight: .bold))
                .padding(.top, line.level <= 2 ? 6 : 2)
        case "list":
            HStack(alignment: .firstTextBaseline, spacing: Space.s) {
                Text("•").foregroundStyle(.secondary)
                text.font(.system(size: 13)).lineSpacing(4)
            }
            .padding(.leading, CGFloat(line.level) * 16)
        case "row":
            text.font(.system(size: 12))
                .padding(.horizontal, Space.s)
                .padding(.vertical, 5)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color(white: 0.965), in: RoundedRectangle(cornerRadius: 4))
        case "header", "footer":
            HStack(alignment: .firstTextBaseline, spacing: Space.s) {
                Text(line.kind == "header" ? "HEADER" : "FOOTER")
                    .font(.system(size: 9, weight: .semibold))
                    .kerning(0.6)
                    .foregroundStyle(.tertiary)
                text.font(.system(size: 11.5)).foregroundStyle(.secondary)
            }
        default:
            text.font(.system(size: 13)).lineSpacing(5)
        }
    }

    private static let labelPattern = try! NSRegularExpression(pattern: #"\[[A-Z]+(?:-[A-Z]+)?(?:-\d+)?\]"#)

    /// Labels become monospaced chips; words awaiting a decision are highlighted.
    private func styled(_ text: String, current: Int? = nil) -> AttributedString {
        let ns = text as NSString
        var marks: [(NSRange, Int)] = Self.labelPattern
            .matches(in: text, range: NSRange(location: 0, length: ns.length)).map { ($0.range, 0) }
        if !pending.isEmpty {
            let words = pending.map(NSRegularExpression.escapedPattern(for:)).joined(separator: "|")
            if let rx = try? NSRegularExpression(pattern: #"(?<![\p{L}\p{N}])(?:"# + words + #")(?![\p{L}\p{N}])"#,
                                                 options: [.caseInsensitive]) {
                marks += rx.matches(in: text, range: NSRange(location: 0, length: ns.length)).map { ($0.range, 1) }
            }
        }
        let needle = search.trimmingCharacters(in: .whitespaces)
        if needle.count >= 2 {
            var range = NSRange(location: 0, length: ns.length)
            var nth = 0
            while true {
                let found = ns.range(of: needle, options: [.caseInsensitive, .diacriticInsensitive, .widthInsensitive], range: range)
                if found.location == NSNotFound { break }
                marks.append((found, nth == current ? 3 : 2))
                nth += 1
                range = NSRange(location: found.location + found.length, length: ns.length - found.location - found.length)
            }
        }
        marks.sort { $0.0.location < $1.0.location }
        var out = AttributedString()
        var cursor = 0
        for (range, kind) in marks where range.location >= cursor {
            out += AttributedString(ns.substring(with: NSRange(location: cursor, length: range.location - cursor)))
            let piece = ns.substring(with: range)
            if kind >= 2 {
                var a = AttributedString(piece)
                // The match on screen is solid; the others are tinted.
                a.backgroundColor = kind == 3 ? Color(red: 1.0, green: 0.78, blue: 0.2) : Color(red: 0.8, green: 0.87, blue: 1.0)
                a.foregroundColor = kind == 3 ? Color(red: 0.25, green: 0.15, blue: 0.0) : Color(red: 0.05, green: 0.2, blue: 0.5)
                out += a  // keeps the line's own font, so headings stay headings
            } else if kind == 0 {
                let attached = range.location > 0 && CharacterSet.letters.contains(ns.substring(with: NSRange(location: range.location - 1, length: 1)).unicodeScalars.first!)
                var a = AttributedString(attached ? "\(piece)\u{2009}" : "\u{2009}\(piece)\u{2009}")
                a.font = .system(size: 11.5, weight: .semibold, design: .monospaced)
                a.backgroundColor = Color(white: 0.925)
                out += a
            } else {
                let isFocused = piece.lowercased() == focused
                var a = AttributedString(piece)
                a.backgroundColor = isFocused ? Color(red: 1.0, green: 0.88, blue: 0.66) : Color(red: 1.0, green: 0.945, blue: 0.84)
                a.foregroundColor = Color(red: 0.54, green: 0.31, blue: 0.0)
                a.underlineStyle = Text.LineStyle(pattern: .solid, color: Color(red: 0.85, green: 0.6, blue: 0.3))
                if isFocused { a.font = .system(size: 13, weight: .semibold) }
                out += a
            }
            cursor = range.location + range.length
        }
        out += AttributedString(ns.substring(from: cursor))
        return out
    }
}

/// A PDF's pages as they will be exported, one after another: masks shown on
/// each, more drawn by dragging, and each page opened to confirm it (D-024).
private struct PDFPages: View {
    @Bindable var model: AppModel
    let item: BatchItem
    let mode: PreviewMode

    var body: some View {
        LazyVStack(spacing: Space.xl) {
            ForEach(item.pictures) { page in
                VStack(alignment: .leading, spacing: Space.s) {
                    Button {
                        model.show(page, in: item)
                    } label: {
                        HStack(spacing: Space.s) {
                            Text(page.title).font(.system(size: 12.5, weight: .semibold))
                            Text(page.awaiting ? "confirm its masks" : "masks confirmed")
                                .font(.system(size: 12))
                                .foregroundStyle(.secondary)
                            if page.image.textLayer == false {
                                Text("text read by OCR").font(.system(size: 12)).foregroundStyle(.tertiary)
                            }
                            Spacer()
                            Image(systemName: page.awaiting ? "circle" : "checkmark.circle.fill")
                                .foregroundStyle(page.awaiting ? Color.secondary : Color.green)
                            Image(systemName: "chevron.right").font(.system(size: 10, weight: .semibold)).foregroundStyle(.tertiary)
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .help("Open \(page.title) to confirm it")
                    ImageCanvas(model: model, picture: page, mode: mode)
                }
                .frame(maxWidth: 960)
            }
        }
    }
}

// MARK: - Decision panel

/// Every unfamiliar word on one list. Words are kept unless marked, so a
/// document needs one scan and one Apply instead of a decision per word.
private struct DecisionPanel: View {
    @Bindable var model: AppModel
    @State private var showResolved = false
    @State private var approvePDF = false
    @State private var approvalPlans: [String: String] = [:]
    @FocusState private var listFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            if model.queue.isEmpty, let item = model.selectedItem, let picture = panelPicture {
                ScrollView {
                    ImagePanel(model: model, item: item, picture: picture)
                        .padding(.horizontal, 28)
                        .padding(.top, 28)
                        .padding(.bottom, Space.l)
                }
                .scrollIndicators(.never)
            } else {
                header
                    .padding(.horizontal, 28)
                    .padding(.top, 28)
                    .padding(.bottom, Space.l)
                if model.queue.isEmpty {
                    ScrollView {
                        done
                            .padding(.horizontal, 28)
                            .padding(.top, Space.l)
                    }
                    .scrollIndicators(.never)
                } else {
                    wordList
                }
            }
            footer
        }
        .animation(.smooth(duration: 0.3), value: model.queue.isEmpty)
        .confirmationDialog("Approve every page of this PDF?", isPresented: $approvePDF) {
            Button("I Reviewed Every Page, Approve Masks") { Task { await model.confirmReviewedPDF(approvalPlans) } }
        } message: {
            Text("Approve only after reviewing the entire sanitized PDF, including logos and screenshots. Final verification still runs before export.")
        }
    }

    /// The picture on screen, if any: an image file or a picture of a Word file.
    private var panelPicture: Picture? { model.currentPicture }

    /// Confirms this picture, then shows the next one still waiting.
    private func confirm(_ picture: Picture) async {
        await model.confirm(picture)
        if let next = model.awaitingPictures.first {
            model.show(next.picture, in: next.item)
        } else if let item = model.selectedItem, !item.isImage {
            model.show(nil, in: item)
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            HStack(alignment: .firstTextBaseline) {
                Text(model.queue.isEmpty ? "Decisions" : "Check words").font(Typeface.sectionTitle).kerning(-0.3)
                Spacer()
                if !model.queue.isEmpty {
                    Text("\(model.queue.count) unfamiliar")
                        .font(.system(size: 13, weight: .medium))
                        .monospacedDigit()
                        .foregroundStyle(.secondary)
                }
            }
            if !model.queue.isEmpty {
                Text("Everything is kept unless you mark it. Mark company and people names to redact them, one at a time or a whole group at once.")
                    .font(Typeface.body)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var wordList: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 2, pinnedViews: .sectionHeaders) {
                    ForEach(WordDecision.Group.allCases, id: \.self) { group in
                        let words = model.queue.filter { $0.group == group }
                        if !words.isEmpty {
                            Section {
                                ForEach(words) { decision in
                                    WordRow(decision: decision,
                                            choice: model.choice(for: decision),
                                            focused: model.focused == decision.id,
                                            systemsRedacted: model.redactSystemNames,
                                            onFocus: { model.focus(decision); listFocused = true },
                                            onChoose: { model.mark(decision, $0); model.focus(decision) })
                                        .id(decision.id)
                                }
                            } header: {
                                HStack(spacing: Space.s) {
                                    SectionLabel(text: group.title)
                                    Text("\(words.count)").font(Typeface.meta).foregroundStyle(.tertiary).monospacedDigit()
                                    Spacer()
                                    GroupActions(group: group, allRedacted: model.allRedacted(in: group)) { choice in
                                        withAnimation(.easeOut(duration: 0.15)) { model.markAll(in: group, choice) }
                                    }
                                }
                                .padding(.horizontal, Space.m)
                                .padding(.top, Space.m)
                                .padding(.bottom, Space.xs)
                                .background(Color(nsColor: .windowBackgroundColor))
                            }
                        }
                    }
                }
                .padding(.horizontal, Space.l)
                .padding(.bottom, Space.l)
            }
            .scrollIndicators(.automatic)
            .focusable()
            .focused($listFocused)
            .focusEffectDisabled()
            .onKeyPress(.downArrow) { move(1, proxy); return .handled }
            .onKeyPress(.upArrow) { move(-1, proxy); return .handled }
            .onKeyPress(characters: CharacterSet(charactersIn: "rRkps")) { press in
                guard let current = currentDecision else { return .ignored }
                switch press.characters {
                case "R":  // the whole group of the focused word
                    let group = current.group
                    model.markAll(in: group, model.allRedacted(in: group) ? .safe : .company(group.bulkLabel))
                case "r": model.mark(current, model.choice(for: current) == .safe ? .company("ORG") : .safe)
                case "p": model.mark(current, .company("PERSON"))
                case "s": model.mark(current, .system)
                default: model.mark(current, .safe)
                }
                return .handled
            }
        }
    }

    private var currentDecision: WordDecision? {
        model.queue.first { $0.id == model.focused }
    }

    /// The display order: grouped, as on screen.
    private var ordered: [WordDecision] {
        WordDecision.Group.allCases.flatMap { g in model.queue.filter { $0.group == g } }
    }

    private func move(_ delta: Int, _ proxy: ScrollViewProxy) {
        let list = ordered
        guard !list.isEmpty else { return }
        let index = list.firstIndex { $0.id == model.focused } ?? -1
        let next = list[min(max(index + delta, 0), list.count - 1)]
        model.focus(next)
        withAnimation(.easeOut(duration: 0.15)) { proxy.scrollTo(next.id, anchor: .center) }
    }

    private var done: some View {
        VStack(alignment: .leading, spacing: Space.s) {
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 28))
                .foregroundStyle(.green)
            Text(model.resolved.isEmpty ? "Nothing to decide" : "Review complete")
                .font(.system(size: 17, weight: .semibold))
            Text(model.resolved.isEmpty
                 ? "Every word in this batch is in the profile, the dictionaries or the technical glossary."
                 : "Your decisions are saved in the profile, so these words are not asked again.")
                .font(Typeface.body)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if let next = model.awaitingPictures.first {
                HStack(spacing: Space.s) {
                    Image(systemName: next.picture.isPage ? "doc.richtext" : "photo").foregroundStyle(.orange)
                    Text(waitingNote)
                    Spacer()
                    Button("Show") { model.show(next.picture, in: next.item) }
                        .buttonStyle(SecondaryButtonStyle())
                }
                .font(Typeface.body)
                .padding(.top, Space.m)
            }
            if !model.resolved.isEmpty {
                DisclosureGroup(isExpanded: $showResolved) {
                    VStack(alignment: .leading, spacing: Space.s) {
                        ForEach(model.resolved) { r in
                            HStack(spacing: Space.s) {
                                Image(systemName: "checkmark")
                                    .font(.system(size: 10, weight: .bold))
                                    .foregroundStyle(.green)
                                Text(r.word)
                                Text(r.result).foregroundStyle(.secondary)
                            }
                            .font(Typeface.body)
                        }
                    }
                    .padding(.top, Space.s)
                } label: {
                    Text("\(model.resolved.count) decisions")
                        .font(Typeface.body)
                        .foregroundStyle(.secondary)
                }
                .padding(.top, Space.m)
            }
        }
    }

    /// "One page needs your confirmation", or images, or both.
    private var waitingNote: String {
        let waiting = model.awaitingPictures.map(\.picture)
        let pages = waiting.filter(\.isPage).count
        let noun = pages == waiting.count ? (pages == 1 ? "page" : "pages")
            : pages == 0 ? (waiting.count == 1 ? "image" : "images") : "images and pages"
        return waiting.count == 1 ? "One \(noun) needs your confirmation." : "\(waiting.count) \(noun) need your confirmation."
    }

    private var footer: some View {
        VStack(spacing: Space.s) {
            if model.queue.isEmpty, let item = model.selectedItem, item.isPDF,
               item.state != .blocked, item.pictures.contains(where: \.awaiting) {
                Button("Approve Reviewed PDF…") {
                    approvalPlans = Dictionary(uniqueKeysWithValues: item.pictures.map { ($0.key, $0.image.digest) })
                    approvePDF = true
                }
                    .buttonStyle(SecondaryButtonStyle())
                    .disabled(model.busy != nil)
            }
            if model.queue.isEmpty, let picture = panelPicture, picture.awaiting {
                Button(picture.isPage ? "Confirm Page" : "Confirm Image") {
                    // Resolve the visible target at action time, including keyboard actions.
                    guard let shown = model.currentPicture else { return }
                    Task { await confirm(shown) }
                }
                    .id(picture.key + "#" + picture.image.digest)
                    .buttonStyle(PrimaryButtonStyle(large: true, fullWidth: true))
                    .disabled(model.busy != nil)
                    .keyboardShortcut(.return, modifiers: .command)
                Text(model.busy.map { "\($0)…" } ?? "Masks are burned into the pixels on export · ⌘↩")
                    .font(Typeface.meta)
                    .foregroundStyle(.secondary)
            } else if model.queue.isEmpty {
                Button("Continue to Export") { model.open(.export) }
                    .buttonStyle(PrimaryButtonStyle(large: true, fullWidth: true))
                    .disabled(!model.canOpen(.export))
                    .keyboardShortcut(.defaultAction)
                Text(exportNote)
                    .font(Typeface.meta)
                    .foregroundStyle(.secondary)
            } else {
                Button("Apply Decisions") { Task { await model.applyMarks() } }
                    .buttonStyle(PrimaryButtonStyle(large: true, fullWidth: true))
                    .disabled(model.busy != nil)
                    .keyboardShortcut(.return, modifiers: .command)
                Text("\(model.queue.count - model.redactCount) kept · \(model.redactCount) redacted · ⌘↩")
                    .font(Typeface.meta)
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.horizontal, 28)
        .padding(.vertical, Space.xl)
        .overlay(alignment: .top) { Divider() }
    }

    private var exportNote: String {
        if let busy = model.busy { return "\(busy)…" }
        if !model.awaitingImages.isEmpty { return "Confirm every image and page to continue." }
        if model.readyCount == 0 { return "No file is ready to export." }
        return "\(model.readyCount) of \(model.items.count) files ready."
    }
}

/// One unfamiliar word: the word, how often it appears, a line of context and
/// the current choice.
private struct WordRow: View {
    let decision: WordDecision
    let choice: WordChoice
    let focused: Bool
    let systemsRedacted: Bool
    let onFocus: () -> Void
    let onChoose: (WordChoice) -> Void

    static let labelTypes = ["ORG", "PERSON", "CLIENT", "SITE", "PROJECT", "PRODUCT", "DOMAIN", "ADDRESS"]

    private var rowContent: some View {
        HStack(alignment: .top, spacing: Space.m) {
            VStack(alignment: .leading, spacing: 3) {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(decision.word)
                        .font(.system(size: 13.5, weight: .semibold))
                        .lineLimit(1)
                    if decision.count > 1 {
                        Text("×\(decision.count)")
                            .font(.system(size: 11.5, weight: .medium))
                            .monospacedDigit()
                            .foregroundStyle(.tertiary)
                    }
                }
                Text(context)
                    .font(Typeface.meta)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            Spacer(minLength: Space.s)
            Menu {
                Button("Keep") { onChoose(.safe) }
                Button(systemsRedacted ? "System name (redacted)" : "Keep as system name") { onChoose(.system) }
                Divider()
                Section("Redact as") {
                    ForEach(Self.labelTypes, id: \.self) { t in
                        Button(t) { onChoose(.company(t)) }
                    }
                }
            } label: {
                ChoiceBadge(choice: choice, systemsRedacted: systemsRedacted)
            }
            .menuStyle(.button)
            .buttonStyle(.plain)
            .menuIndicator(.hidden)
            .fixedSize()
        }
        .padding(.vertical, 9)
        .padding(.horizontal, Space.m)
        .background(focused ? Color.primary.opacity(0.06) : .clear,
                    in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .contentShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        .onTapGesture(perform: onFocus)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(decision.word), \(ChoiceBadge.text(choice, systemsRedacted))")
        .accessibilityHint(String(decision.context.prefix(220)))

    }

    private var keepActions: some View {
        rowContent
            .accessibilityAction { onFocus() }
            .accessibilityAction(named: Text("Keep word")) { onChoose(.safe) }
            .accessibilityAction(named: Text("Keep system name")) { onChoose(.system) }
    }

    private var nameActions: some View {
        keepActions
            .accessibilityAction(named: Text("Redact as PERSON")) { onChoose(.company("PERSON")) }
            .accessibilityAction(named: Text("Redact as ORG")) { onChoose(.company("ORG")) }
            .accessibilityAction(named: Text("Redact as CLIENT")) { onChoose(.company("CLIENT")) }
    }

    var body: some View {
        nameActions
            .accessibilityAction(named: Text("Redact as SITE")) { onChoose(.company("SITE")) }
            .accessibilityAction(named: Text("Redact as PROJECT")) { onChoose(.company("PROJECT")) }
            .accessibilityAction(named: Text("Redact as PRODUCT")) { onChoose(.company("PRODUCT")) }
            .accessibilityAction(named: Text("Redact as DOMAIN")) { onChoose(.company("DOMAIN")) }
            .accessibilityAction(named: Text("Redact as ADDRESS")) { onChoose(.company("ADDRESS")) }
    }

    private var context: AttributedString {
        var a = AttributedString(decision.context)
        if let range = a.range(of: decision.word, options: .caseInsensitive) {
            a[range].foregroundColor = .primary
            a[range].font = .system(size: 12, weight: .semibold)
        }
        return a
    }
}

/// "Redact all" for one group, with the other labels and "Keep all" in a menu.
/// Once every word is marked, the button keeps them all again.
private struct GroupActions: View {
    let group: WordDecision.Group
    let allRedacted: Bool
    let choose: (WordChoice) -> Void

    var body: some View {
        HStack(spacing: 2) {
            Button(allRedacted ? "Keep all" : "Redact all") {
                choose(allRedacted ? .safe : .company(group.bulkLabel))
            }
            .buttonStyle(.plain)
            .padding(.leading, 9)
            .padding(.trailing, 7)
            .frame(height: 22)
            .help(allRedacted ? "Keep every word in this group" : "Redact every word in this group as \(group.bulkLabel) (⇧R)")
            Menu {
                Section("Redact all as") {
                    ForEach(WordRow.labelTypes, id: \.self) { label in
                        Button(label) { choose(.company(label)) }
                    }
                }
                Divider()
                Button("Keep All") { choose(.safe) }
            } label: {
                Image(systemName: "chevron.down").font(.system(size: 8, weight: .bold))
                    .frame(width: 18, height: 22)
                    .contentShape(Rectangle())
            }
            .menuStyle(.button)
            .buttonStyle(.plain)
            .menuIndicator(.hidden)
            .fixedSize()
            .padding(.trailing, 3)
        }
        .font(.system(size: 11.5, weight: .semibold))
        .foregroundStyle(.primary)
        .background(Color.primary.opacity(0.07), in: Capsule())
        .accessibilityElement(children: .contain)
    }
}

/// Keep is quiet; Redact is solid, so marked words stand out in the list.
private struct ChoiceBadge: View {
    let choice: WordChoice
    let systemsRedacted: Bool

    static func text(_ choice: WordChoice, _ systemsRedacted: Bool) -> String {
        switch choice {
        case .safe: "Keep"
        case .system: systemsRedacted ? "System · redact" : "System"
        case .company(let label): "Redact · \(label)"
        }
    }

    private var redacts: Bool {
        switch choice {
        case .company: true
        case .system: systemsRedacted
        case .safe: false
        }
    }

    var body: some View {
        HStack(spacing: 4) {
            Text(Self.text(choice, systemsRedacted))
            Image(systemName: "chevron.up.chevron.down").font(.system(size: 8, weight: .bold))
        }
        .font(.system(size: 11.5, weight: .semibold))
        .padding(.horizontal, 9)
        .frame(height: 24)
        .foregroundStyle(redacts ? Color(nsColor: .windowBackgroundColor) : .primary)
        .background(redacts ? Color.primary : Color.primary.opacity(0.07), in: Capsule())
        .contentShape(Capsule())
    }
}

/// The sidebar rows outside the split view, for design snapshots only.
struct SidebarRowsPreview: View {
    let items: [BatchItem]

    var body: some View {
        VStack(alignment: .leading, spacing: Space.xs) {
            HStack {
                Text("Batch")
                Spacer()
                Text("\(items.count) files")
            }
            .font(.system(size: 11, weight: .semibold))
            .foregroundStyle(.secondary)
            .padding(.horizontal, Space.s)
            .padding(.bottom, Space.xs)
            ForEach(items) { item in
                SidebarFileRow(item: item)
                    .padding(.horizontal, Space.s)
                    .padding(.vertical, Space.xxs)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(item.id == items.first?.id ? Color.primary.opacity(0.08) : .clear,
                                in: RoundedRectangle(cornerRadius: 8, style: .continuous))
            }
        }
        .padding(Space.m)
        .frame(width: 264)
        .background(Color(nsColor: .windowBackgroundColor))
    }
}
