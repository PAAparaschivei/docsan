import AppKit
import Observation
import SwiftUI

enum Step: Int, CaseIterable, Hashable {
    case drop, review, export

    var title: String {
        switch self {
        case .drop: "Drop"
        case .review: "Review"
        case .export: "Export"
        }
    }
}

/// Light, dark or following macOS. Document pages stay light either way.
/// What happens to pictures and diagrams in Word files.
enum VisualMode: String, CaseIterable, Hashable, Identifiable {
    case keep, labelUnreadable, labelAll

    var id: String { rawValue }

    var title: String {
        switch self {
        case .keep: "Keep them, with names masked"
        case .labelUnreadable: "Keep them, label what DocSan can't read"
        case .labelAll: "Replace them all with labels"
        }
    }

    var detail: String {
        switch self {
        case .keep:
            "Pictures and drawings keep their content and names in them are masked; charts become tables and SmartArt lists. A visual DocSan can't read stops the file."
        case .labelUnreadable:
            "As above, but a visual DocSan can't read, such as a WMF picture or an equation, becomes a label like [IMAGE-REMOVED] instead of stopping the file."
        case .labelAll:
            "Every picture, chart and diagram becomes a label such as [IMAGE-REMOVED]. Only the text is kept."
        }
    }
}

enum Appearance: String, CaseIterable, Hashable {
    case system, light, dark

    var title: String {
        switch self {
        case .system: "Match System"
        case .light: "Light"
        case .dark: "Dark"
        }
    }

    var symbol: String {
        switch self {
        case .system: "circle.lefthalf.filled"
        case .light: "sun.max"
        case .dark: "moon"
        }
    }

    @MainActor func apply() {
        NSApp.appearance = switch self {
        case .system: nil
        case .light: NSAppearance(named: .aqua)
        case .dark: NSAppearance(named: .darkAqua)
        }
    }
}

enum PreviewMode: String, CaseIterable, Hashable {
    case original = "Original"
    case sanitized = "Sanitized"
}

/// The document as pages (rendered by Quick Look) or as text with every word to find and decide.
enum PreviewLayout: String, CaseIterable, Hashable {
    case pages, text

    var symbol: String { self == .pages ? "doc.richtext" : "text.alignleft" }
    var help: String { self == .pages ? "Pages, as the file looks" : "Text, to find and decide words" }
}

/// How an unfamiliar word is classified.
enum WordChoice: Hashable {
    case company(String)  // replaced with a typed label
    case system  // kept unless system names are redacted
    case safe  // kept everywhere
}

struct Resolution: Identifiable, Hashable {
    let id = UUID()
    let word: String
    let result: String
}

/// An unfamiliar word that needs a decision, gathered across the whole batch.
struct WordDecision: Identifiable, Hashable {
    let word: String
    let context: String
    let files: Int
    let count: Int
    let source: String  // first file that contains the word
    var kind: String? = nil  // "place" when the engine knows it as a place
    var id: String { word.lowercased() }

    enum Group: Int, CaseIterable {
        case names, products, codes, places, other

        var title: String {
            switch self {
            case .names: "Possible names"
            case .products: "Products and systems"
            case .codes: "Acronyms and codes"
            case .places: "Places"
            case .other: "Other words"
            }
        }

        /// Where a word starts before the owner touches it. Possible names are
        /// redacted (as first name and initial, D-021), mixed-case product
        /// names are kept as system names and the rest are kept.
        var defaultChoice: WordChoice {
            switch self {
            case .names: .company("PERSON")
            case .products: .system
            case .codes, .places, .other: .safe
            }
        }

        /// The label "Redact all" uses; each row can still be changed.
        var bulkLabel: String {
            switch self {
            case .names: "PERSON"
            case .products: "PRODUCT"
            case .places: "SITE"
            case .codes, .other: "ORG"
            }
        }
    }

    /// Capitalised words are the likeliest names, so they are listed first.
    var group: Group {
        if kind == "place" { return .places }
        let letters = word.filter(\.isLetter)
        if word.contains(where: \.isNumber) || (letters.count >= 2 && letters == letters.uppercased()) { return .codes }
        if word.dropFirst().contains(where: \.isUppercase) { return .products }  // OneIdentity, ZSmart
        if word.first?.isUppercase == true { return .names }
        return .other
    }
}

/// One image the owner confirms: an image file, a picture inside a Word file or a page of a PDF.
struct Picture: Identifiable, Hashable {
    let key: String  // the image file's path, "<Word file path>#image-<n>" or "<PDF path>#page-<n>"
    let image: EngineImage
    let source: String  // the pixels to show
    let index: Int?  // position in the Word file, or the page number
    var id: String { key }
    var awaiting: Bool { !image.confirmed }
    var isRemoved: Bool { image.removed != nil }
    var isPage: Bool { key.contains("#page-") }
    /// A picture inside a Word file, which can also be removed whole.
    var isWordPicture: Bool { index != nil && !isPage }
    var title: String { isPage ? "Page \(index ?? 0)" : "Image \(index ?? 0)" }
}

/// The owner's choices for one image: masks they drew, the plan they
/// confirmed and, for a picture in a Word file, "remove" (whole) or "keep".
struct ImageChoice: Hashable {
    var masks: [CGRect] = []
    var confirmed: String?
    var picture: String?

    var payload: [String: Any] {
        var out: [String: Any] = ["masks": masks.map { [$0.minX, $0.minY, $0.width, $0.height] }]
        if let confirmed { out["confirmed"] = confirmed }
        if let picture { out["picture"] = picture }
        return out
    }
}

/// One file of the batch, as the engine last reported it.
struct BatchItem: Identifiable, Hashable {
    var file: EngineFile
    var id: String { file.source }
    var name: String { URL(fileURLWithPath: file.source).lastPathComponent }
    var isImage: Bool { file.kind == "image" }
    var isPDF: Bool { file.kind == "pdf" }

    /// The images to confirm: the file itself, or each picture in a Word file.
    var pictures: [Picture] {
        if isImage, let image = file.image { return [Picture(key: file.source, image: image, source: file.source, index: nil)] }
        return file.images.compactMap { image in
            guard let key = image.key, let source = image.source else { return nil }
            return Picture(key: key, image: image, source: source, index: image.index)
        }
    }

    /// Words are settled and at least one image waits for the owner's confirmation.
    var awaitingConfirmation: Bool {
        state == .review && file.review.isEmpty && pictures.contains(where: \.awaiting)
    }

    var state: FileState {
        switch file.status {
        case "ready": .ready
        case "needs_review": .review
        default: .blocked
        }
    }

    var symbol: String {
        switch URL(fileURLWithPath: file.source).pathExtension.lowercased() {
        case "png", "jpg", "jpeg": "photo"
        case "pdf": "doc.richtext"
        case "doc", "rtf", "odt": "doc"
        default: "doc.text"
        }
    }

    var detail: String {
        switch state {
        case .ready:
            if isImage { return "Ready · masks confirmed" }
            if isPDF { return "Ready · \(pictures.count) \(pictures.count == 1 ? "page" : "pages") confirmed" }
            let removed = pictures.filter(\.isRemoved).count, masked = pictures.count - removed
            var parts = ["Ready"]
            if masked > 0 { parts.append("\(masked) \(masked == 1 ? "image" : "images") masked") }
            if removed > 0 { parts.append("\(removed) removed") }
            return parts.joined(separator: " · ")
        case .review:
            guard awaitingConfirmation else { return "Needs review · \(file.review.count)" }
            let n = pictures.filter(\.awaiting).count
            if isPDF { return "Confirm \(n == 1 ? "1 page" : "\(n) pages")" }
            return isImage ? "Confirm masks" : "Confirm \(n == 1 ? "1 image" : "\(n) images")"
        case .blocked:
            return "Blocked · \(blockReason)"
        }
    }

    /// A short, plain reason for the sidebar; the full list is in the preview.
    var blockReason: String {
        let all = file.reasons.joined(separator: " ").lowercased()
        if all.contains("not supported yet") { return "format not supported yet" }
        if isPDF {
            if all.contains("encrypted") { return "encrypted PDF" }
            if all.contains("xfa form") { return "XFA form" }
            if all.contains("portfolio") { return "PDF portfolio" }
            if all.contains("never applied") { return "redactions never applied" }
            if all.contains("postscript") || all.contains("another file") { return "content DocSan can't draw" }
            if all.contains("does not know") { return "unknown PDF part" }
            if all.contains("not a pdf") || all.contains("cut short") || all.contains("damaged") || all.contains("cannot read the pdf") {
                return "PDF cannot be read"
            }
            if all.contains("pages; split it") { return "too many pages" }
        }
        if all.contains("encrypted") { return "encrypted or legacy file" }
        if all.contains("embedded object") { return "contains embedded objects" }
        if all.contains("chart") { return "contains charts" }
        if all.contains("smartart") { return "contains SmartArt" }
        if all.contains("equation") { return "contains equations" }
        if all.contains("unsupported drawing") || all.contains("wordart") || all.contains("ink content") { return "contains drawings" }
        if all.contains("vector image") { return "contains vector images" }
        if all.contains("cannot be drawn") || all.contains("emf+ only") || all.contains("cannot be read yet") { return "contains drawings DocSan can't draw" }
        if !isImage && !isPDF && all.contains("image") { return "contains images" }
        if all.contains("verifier found") { return "references remain" }
        if all.contains("cannot read the image") || all.contains("not a png or jpeg") { return "image cannot be read" }
        if all.contains("inspection incomplete") { return "inspection incomplete" }
        if let first = file.reasons.first {
            let plain = first.replacingOccurrences(of: #"^[\w/\[\]._-]+\.xml: "#, with: "", options: .regularExpression)
            return plain.count > 60 ? String(plain.prefix(57)) + "…" : plain
        }
        return "unsupported content"
    }

    /// Blocked only because of visuals, which the placeholder option can remove.
    var blockedByVisuals: Bool {
        !isPDF && state == .blocked && ["contains images", "contains charts", "contains SmartArt", "contains embedded objects",
                              "contains equations", "contains drawings", "contains vector images",
                              "contains drawings DocSan can't draw"].contains(blockReason)
    }
}

struct ExportResult: Hashable {
    let folder: URL
    let files: [String]
}

@MainActor
@Observable
final class AppModel {
    var step: Step = .drop
    var busy: String?
    var errorMessage: String?
    var profile: EngineProfile?
    var items: [BatchItem] = []
    var selectedFile: BatchItem.ID?
    var queue: [WordDecision] = []
    var resolved: [Resolution] = []
    var exported: ExportResult?
    var visuals: VisualMode {
        didSet { defaults.set(visuals.rawValue, forKey: "visuals") }
    }
    var appearance: Appearance {
        didSet {
            defaults.set(appearance.rawValue, forKey: "appearance")
            appearance.apply()
        }
    }

    private var choices: [String: WordChoice] = [:]
    /// Per picture key: masks the owner drew and the plan they confirmed.
    private(set) var imageChoices: [String: ImageChoice] = [:]
    /// The picture shown instead of the document text, if any.
    var selectedPicture: String?
    var searchText = ""
    var previewMode: PreviewMode = .sanitized
    var previewLayout: PreviewLayout = .pages
    /// Which search match is on screen.
    var matchIndex = 0
    var profileWords: ProfileWords?
    var showingWords = false
    private var inputs: [URL] = []
    let storage: Storage?
    let engine: EngineClient?
    private let defaults: UserDefaults

    init(storage: Storage? = try? Storage.standard(), engine: EngineClient? = EngineClient.bundled(),
         defaults: UserDefaults = .standard) {
        self.storage = storage
        self.engine = engine
        self.defaults = defaults
        // "removeImages" dates from before pictures could be masked; it no longer applies.
        defaults.removeObject(forKey: "removeImages")
        self.visuals = VisualMode(rawValue: defaults.string(forKey: "visuals") ?? "") ?? .keep
        self.appearance = Appearance(rawValue: defaults.string(forKey: "appearance") ?? "") ?? .system
    }

    // MARK: Derived state

    var batchLoaded: Bool { !items.isEmpty }
    var redactSystemNames: Bool { profile?.redactSystemNames ?? false }
    var reviewTotal: Int { resolved.count + queue.count }
    var readyCount: Int { items.filter { $0.state == .ready }.count }
    var blockedCount: Int { items.filter { $0.state == .blocked }.count }
    var selectedItem: BatchItem? { items.first { $0.id == selectedFile } ?? items.first }
    var engineMissing: Bool { engine == nil || storage == nil }
    var awaitingImages: [BatchItem] { items.filter(\.awaitingConfirmation) }
    var personStyle: String { profile?.personStyle ?? "first_name_initial" }

    /// The picture on screen: the image file itself, or the chosen picture of a Word file.
    var currentPicture: Picture? {
        guard let item = selectedItem else { return nil }
        if item.isImage { return item.pictures.first }
        return item.pictures.first { $0.key == selectedPicture }
    }

    /// Every picture still waiting, in batch order.
    var awaitingPictures: [(item: BatchItem, picture: Picture)] {
        items.filter { $0.awaitingConfirmation }.flatMap { item in item.pictures.filter(\.awaiting).map { (item, $0) } }
    }

    func show(_ picture: Picture?, in item: BatchItem) {
        selectedFile = item.id
        selectedPicture = item.isImage ? nil : picture?.key
    }

    private var imagesPayload: [String: Any] {
        imageChoices.mapValues { $0.payload }
    }

    func canOpen(_ target: Step) -> Bool {
        switch target {
        case .drop: true
        case .review: batchLoaded
        case .export: batchLoaded && queue.isEmpty && busy == nil && !items.contains { $0.state == .review }
        }
    }

    func open(_ target: Step) {
        if canOpen(target) { step = target }
    }

    // MARK: Profile

    func loadProfile() async {
        guard let engine, let storage, FileManager.default.fileExists(atPath: storage.profile.path) else { return }
        await perform("Reading the profile") {
            let response = try await engine.call(["command": "profile-info", "profile": storage.profile.path],
                                                 as: ProfileResponse.self)
            self.profile = response.profile
        }
    }

    func importProfile(_ url: URL) async {
        guard let storage else { return }
        do {
            try storage.importProfile(from: url)
        } catch {
            errorMessage = "The profile could not be copied: \(error.localizedDescription)"
            return
        }
        profile = nil
        await loadProfile()
        if profile == nil { try? FileManager.default.removeItem(at: storage.profile) }
    }

    func createProfile(name: String, aliases: [String], people: [String]) async {
        guard let engine, let storage else { return }
        try? FileManager.default.removeItem(at: storage.profile)
        await perform("Creating the profile") {
            let response = try await engine.call([
                "command": "create-profile", "profile": storage.profile.path,
                "name": name, "aliases": aliases, "people": people,
            ], as: ProfileResponse.self)
            self.profile = response.profile
        }
    }

    func setRedactSystemNames(_ value: Bool) async {
        guard let engine, let storage, profile != nil else { return }
        await perform("Updating the policy") {
            let response = try await engine.call([
                "command": "set-policy", "profile": storage.profile.path, "redact_system_names": value,
            ], as: ProfileResponse.self)
            self.profile = response.profile
        }
        if batchLoaded { await analyze() }
    }

    // MARK: Batch

    func addFiles(_ urls: [URL]) async {
        guard let storage, profile != nil else { return }
        do {
            let inbox = try storage.newFolder("inbox")
            let copied = try storage.importFiles(urls, into: inbox)
            guard !copied.isEmpty else {
                errorMessage = "No files were found to add."
                return
            }
            inputs = copied
        } catch {
            errorMessage = "The files could not be copied: \(error.localizedDescription)"
            return
        }
        queue = []
        resolved = []
        choices = [:]
        marks = [:]
        imageChoices = [:]
        exported = nil
        await analyze()
        if batchLoaded {
            if queue.isEmpty, let next = awaitingPictures.first {
                show(next.picture, in: next.item)
            }
            step = .review
        }
    }

    private func watchProgress() -> Task<Void, Never> {
        guard let storage else { return Task {} }
        let path = storage.privateDir.appendingPathComponent("progress.json")
        try? FileManager.default.removeItem(at: path)
        return Task { @MainActor in
            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(400))
                guard !Task.isCancelled else { break }
                if let data = try? Data(contentsOf: path),
                   let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                   let stage = value["stage"] as? String {
                    let current = value["current"] as? Int ?? 0, total = value["total"] as? Int ?? 0
                    self.busy = total > 0 ? "\(stage) · \(current) of \(total)" : stage
                }
            }
        }
    }

    /// Runs a dry run, filters dictionary words through the macOS spell checker
    /// and builds the decision queue from what remains.
    func analyze() async {
        guard let engine, let storage, !inputs.isEmpty else { return }
        // A revised plan must never inherit a previous export's verified state.
        // Keep the saved file, but require a fresh export for the current plan.
        exported = nil
        let progress = watchProgress()
        defer { progress.cancel() }
        await perform("Checking files") {
            var response = try await self.analyzeOnce(engine, storage)
            let candidates = Set(response.files.flatMap { $0.review.map(\.word) })
            let known = Dictionaries.knownWords(Array(candidates))
            if !known.isEmpty {
                try storage.addKnownWords(known)
                response = try await self.analyzeOnce(engine, storage)
            }
            self.items = response.files.map(BatchItem.init)
            if self.selectedFile == nil || !self.items.contains(where: { $0.id == self.selectedFile }) {
                self.selectedFile = self.items.first?.id
            }
            var seen = Set<String>()
            var pending: [WordDecision] = []
            for file in response.files {
                for r in file.review where !seen.contains(r.word.lowercased()) {
                    seen.insert(r.word.lowercased())
                    let matches = response.files.flatMap { $0.review.filter { $0.word.lowercased() == r.word.lowercased() } }
                    let files = response.files.filter { $0.review.contains { $0.word.lowercased() == r.word.lowercased() } }.count
                    pending.append(WordDecision(word: r.word, context: r.context, files: files,
                                                count: matches.map(\.count).reduce(0, +), source: file.source, kind: r.kind))
                }
            }
            self.queue = pending  // Never hide an item the engine still reports as unresolved.
                .sorted { ($0.group.rawValue, -$0.count, $0.word.lowercased()) < ($1.group.rawValue, -$1.count, $1.word.lowercased()) }
            self.marks = self.marks.filter { id, _ in self.queue.contains { $0.id == id } }
            if let first = self.queue.first {
                self.focus(first)
            }
            // Large Word HTML trees are expensive to traverse. Keep native
            // text review responsive; Pages remains available explicitly.
            if let selected = self.selectedItem, !selected.isPDF,
               !self.queue.isEmpty || selected.pictures.count > 20 {
                self.previewLayout = .text
            }
        }
    }

    // MARK: List review

    /// Words marked for redaction; every other word in the list is kept.
    var marks: [String: WordChoice] = [:]
    var focused: String?

    func choice(for decision: WordDecision) -> WordChoice { marks[decision.id] ?? decision.group.defaultChoice }
    var redactCount: Int { queue.filter { if case .company = choice(for: $0) { true } else { false } }.count }

    func mark(_ decision: WordDecision, _ choice: WordChoice) {
        marks[decision.id] = choice
    }

    /// Gives every word in a group the same choice, such as redacting all possible names.
    func markAll(in group: WordDecision.Group, _ choice: WordChoice) {
        for decision in queue where decision.group == group { mark(decision, choice) }
    }

    func allRedacted(in group: WordDecision.Group) -> Bool {
        let words = queue.filter { $0.group == group }
        return !words.isEmpty && words.allSatisfy { if case .company = choice(for: $0) { true } else { false } }
    }

    /// Shows the word in the document preview.
    func focus(_ decision: WordDecision) {
        focused = decision.id
        selectedFile = decision.source
    }

    /// Saves every decision in the list at once: marked words are redacted,
    /// all others are kept and remembered, so they are not asked again.
    func applyMarks() async {
        for decision in queue {
            let choice = choice(for: decision)
            choices[decision.id] = choice
            let summary = switch choice {
            case .company("PERSON") where personStyle == "first_name_initial": "first name and initial"
            case .company(let label): "[\(label)]"
            case .system: redactSystemNames ? "system name, redacted" : "system name, kept"
            case .safe: "kept"
            }
            resolved.insert(Resolution(word: decision.word, result: summary), at: 0)
        }
        queue = []
        marks = [:]
        focused = nil
        await applyDecisions()
        // Words settled: show the first picture that still needs confirming.
        if let next = awaitingPictures.first { show(next.picture, in: next.item) }
    }

    private func analyzeOnce(_ engine: EngineClient, _ storage: Storage) async throws -> BatchResponse {
        try await engine.call([
            "command": "analyze", "inputs": inputs.map(\.path), "profile": storage.profile.path,
            "lexicon": storage.lexicon.path, "private": storage.privateDir.path, "remove_images": visuals == .labelAll,
            "label_unreadable": visuals == .labelUnreadable, "images": imagesPayload,
        ], as: BatchResponse.self)
    }

    // MARK: Image masks

    /// Adds a mask the owner drew, in pixels of the upright image. Any change
    /// withdraws the confirmation, so the owner confirms the final plan.
    func addMask(_ rect: CGRect, to picture: Picture) async {
        let bounds = CGRect(x: 0, y: 0, width: picture.image.width, height: picture.image.height)
        let clipped = rect.standardized.intersection(bounds).integral
        guard clipped.width >= 2, clipped.height >= 2 else { return }
        imageChoices[picture.key, default: ImageChoice()].masks.append(clipped)
        imageChoices[picture.key]?.confirmed = nil
        await analyze()
    }

    func removeMask(_ rect: CGRect, from picture: Picture) async {
        guard var choice = imageChoices[picture.key] else { return }
        choice.masks.removeAll { $0.integral == rect.integral }
        choice.confirmed = nil
        imageChoices[picture.key] = choice
        await analyze()
    }

    /// Covers every place where the verifier's OCR read a reference.
    func maskFound(in picture: Picture) async {
        guard !picture.image.found.isEmpty else { return }
        var choice = imageChoices[picture.key] ?? ImageChoice()
        choice.masks += picture.image.foundRects.map { $0.insetBy(dx: -4, dy: -4).integral }
        choice.confirmed = nil
        imageChoices[picture.key] = choice
        await analyze()
    }

    /// Confirms exactly the masks on screen; the engine releases only this plan.
    func confirm(_ picture: Picture) async {
        guard busy == nil, let shown = currentPicture,
              shown.key == picture.key, shown.image.digest == picture.image.digest,
              let index = items.firstIndex(where: { $0.pictures.contains(where: { $0.key == picture.key }) }) else { return }
        imageChoices[picture.key, default: ImageChoice()].confirmed = picture.image.digest
        var item = items[index]
        if item.isImage { item.file.image?.confirmed = true }
        else if let n = item.file.images.firstIndex(where: { $0.key == picture.key }) { item.file.images[n].confirmed = true }
        // Approval changes no pixels. The export command still recomputes the
        // plan, checks this digest and independently verifies the saved bytes.
        if item.file.status == "needs_review", item.file.review.isEmpty,
           item.pictures.allSatisfy({ !$0.awaiting }) {
            item.file.status = "ready"
            item.file.reasons = []
        }
        items[index] = item
    }

    /// One explicit approval for a PDF already reviewed as a whole. Each page
    /// keeps its own file/page/mask digest; export checks all of them again.
    func confirmReviewedPDF(_ expected: [String: String]) async {
        guard busy == nil, queue.isEmpty, let item = selectedItem, item.isPDF,
              item.state != .blocked, item.pictures.allSatisfy({ $0.image.found.isEmpty }) else { return }
        let pages = item.pictures
        guard expected.count == pages.count, pages.allSatisfy({ expected[$0.key] == $0.image.digest }) else { return }
        for page in pages {
            guard let current = items.first(where: { $0.id == item.id })?.pictures.first(where: { $0.key == page.key }),
                  current.image.digest == page.image.digest else { return }
            show(current, in: item)
            await confirm(current)
        }
        show(nil, in: item)
    }

    /// Removes a picture of a Word file whole, or keeps one DocSan would
    /// remove, with its masks to confirm.
    func setRemoved(_ remove: Bool, _ picture: Picture) async {
        imageChoices[picture.key, default: ImageChoice()].picture = remove ? "remove" : "keep"
        imageChoices[picture.key]?.confirmed = nil
        await analyze()
    }

    func withdrawConfirmation(_ picture: Picture) async {
        imageChoices[picture.key]?.confirmed = nil
        await analyze()
    }

    // MARK: Search

    struct SearchResult: Identifiable, Hashable {
        let item: BatchItem
        let original: Int
        let output: Int
        var id: String { item.id }
    }

    /// How often the search text appears in each file before and after
    /// sanitizing, ignoring case and accents. Images count what recognition
    /// read, and what no mask covers.
    var searchResults: [SearchResult] {
        let needle = searchText.trimmingCharacters(in: .whitespaces)
        guard needle.count >= 2 else { return [] }
        return items.map { item in
            // A PDF's text is its pages' text.
            var before = item.isPDF ? [] : item.file.previewOriginal.map(\.text)
            var after = item.isPDF ? [] : item.file.previewSanitized.map(\.text)
            for picture in item.pictures where !item.isImage {
                before += picture.image.lines.map(\.text)
                after += picture.image.visible
            }
            return SearchResult(item: item, original: Self.count(needle, in: before), output: Self.count(needle, in: after))
        }
    }

    /// One place where the search text occurs: a line of the document preview,
    /// or a line read in a picture.
    struct SearchMatch: Hashable {
        let file: String
        let picture: String?  // the picture's key, for a match inside a picture
        let line: Int
        let occurrence: Int  // which occurrence within that line
    }

    var searchNeedle: String { searchText.trimmingCharacters(in: .whitespaces) }

    /// Every occurrence in the current view (Original or Sanitized), in batch
    /// order; a picture's matches come where the picture sits in the document.
    var searchMatches: [SearchMatch] {
        let needle = searchNeedle
        guard needle.count >= 2 else { return [] }
        var out: [SearchMatch] = []
        func add(_ texts: [String], file: String, picture: String?) {
            for (line, text) in texts.enumerated() {
                for k in 0..<Self.count(needle, in: [text]) {
                    out.append(SearchMatch(file: file, picture: picture, line: line, occurrence: k))
                }
            }
        }
        func texts(_ picture: Picture) -> [String] {
            previewMode == .original ? picture.image.lines.map(\.text) : picture.image.visible
        }
        for item in items {
            if item.isImage {
                if let picture = item.pictures.first { add(texts(picture), file: item.id, picture: picture.key) }
                continue
            }
            if item.isPDF {
                for page in item.pictures { add(texts(page), file: item.id, picture: page.key) }
                continue
            }
            let lines = previewMode == .original ? item.file.previewOriginal : item.file.previewSanitized
            for (i, line) in lines.enumerated() {
                if line.kind == "image", let picture = item.pictures.first(where: { $0.index == line.level }) {
                    add(texts(picture), file: item.id, picture: picture.key)
                } else {
                    for k in 0..<Self.count(needle, in: [line.text]) {
                        out.append(SearchMatch(file: item.id, picture: nil, line: i, occurrence: k))
                    }
                }
            }
        }
        return out
    }

    var currentMatch: SearchMatch? {
        let matches = searchMatches
        return matches.isEmpty ? nil : matches[min(matchIndex, matches.count - 1)]
    }

    /// Moves to the next or previous match and shows it.
    func goToMatch(_ delta: Int) {
        let matches = searchMatches
        guard !matches.isEmpty else { return }
        previewLayout = .text  // matches are highlighted in the text
        matchIndex = ((min(matchIndex, matches.count - 1) + delta) % matches.count + matches.count) % matches.count
        reveal(matches[matchIndex])
    }

    func reveal(_ match: SearchMatch) {
        selectedFile = match.file
        if let item = items.first(where: { $0.id == match.file }), !item.isImage {
            selectedPicture = match.picture
        }
    }

    /// What the profile does with a word: "safe", a label, or nil when it is not in the profile.
    func profileChoice(for word: String) -> String? {
        guard let words = profileWords else { return nil }
        if let term = words.terms.first(where: { $0.text.compare(word, options: [.caseInsensitive, .diacriticInsensitive]) == .orderedSame }) {
            return term.label
        }
        if words.safe.contains(where: { $0.word.compare(word, options: [.caseInsensitive, .diacriticInsensitive]) == .orderedSame }) {
            return "safe"
        }
        return nil
    }

    /// Keeps or replaces a searched word everywhere, through the profile.
    func decide(_ word: String, as choice: String) async {
        await reclassify([word: choice])
    }

    static func count(_ needle: String, in texts: [String]) -> Int {
        texts.reduce(0) { total, text in
            var n = 0
            var range = text.startIndex..<text.endIndex
            while let found = text.range(of: needle, options: [.caseInsensitive, .diacriticInsensitive, .widthInsensitive], range: range) {
                n += 1
                range = found.upperBound..<text.endIndex
            }
            return total + n
        }
    }

    // MARK: Profile words

    func loadProfileWords() async {
        guard let engine, let storage, profile != nil else { return }
        await perform("Reading the profile") {
            self.profileWords = try await engine.call(["command": "profile-words", "profile": storage.profile.path],
                                                      as: ProfileWordsResponse.self).words
        }
    }

    /// Changes words the profile already knows: "safe", "remove" or a label.
    func reclassify(_ changes: [String: String]) async {
        guard let engine, let storage, !changes.isEmpty else { return }
        await perform("Updating the profile") {
            let response = try await engine.call([
                "command": "reclassify", "profile": storage.profile.path, "changes": changes,
            ], as: ProfileWordsResponse.self)
            self.profileWords = response.words
        }
        await loadProfile()
        if batchLoaded { await analyze() }
    }

    func setPersonStyle(_ style: String) async {
        guard let engine, let storage, profile != nil else { return }
        await perform("Updating the policy") {
            let response = try await engine.call([
                "command": "set-policy", "profile": storage.profile.path, "person_style": style,
            ], as: ProfileResponse.self)
            self.profile = response.profile
        }
        if batchLoaded { await analyze() }
    }

    /// Records every decision in the profile, then checks the batch again.
    func applyDecisions() async {
        guard let engine, let storage else { return }
        var company: [String: String] = [:]
        var safe: [String] = []
        var systems: [String] = []
        for r in resolved {
            switch choices[r.word.lowercased()] {
            case .company(let label): company[r.word] = label
            case .system: systems.append(r.word)
            case .safe: safe.append(r.word)
            case .none: break
            }
        }
        await perform("Saving decisions") {
            let response = try await engine.call([
                "command": "classify", "profile": storage.profile.path,
                "company": company, "safe": safe, "systems": systems,
            ], as: ProfileResponse.self)
            self.profile = response.profile
            self.profileWords = nil  // read again when needed
        }
        await analyze()
    }

    func export(to folder: URL) async {
        guard let engine, let storage else { return }
        let progress = watchProgress()
        defer { progress.cancel() }
        await perform("Exporting verified files") {
            let outbox = try storage.newFolder("outbox")
            let response = try await engine.call([
                "command": "export", "inputs": self.inputs.map(\.path), "profile": storage.profile.path,
                "lexicon": storage.lexicon.path, "private": storage.privateDir.path,
                "remove_images": self.visuals == .labelAll, "label_unreadable": self.visuals == .labelUnreadable,
                "out": outbox.path, "images": self.imagesPayload,
            ], as: BatchResponse.self)
            self.items = response.files.map(BatchItem.init)
            let released = response.files.compactMap { f -> (url: URL, sha256: String)? in
                guard let name = f.output, let sha = f.sha256 else { return nil }
                return (outbox.appendingPathComponent(name), sha)
            }
            guard !released.isEmpty else { throw EngineError.failed("No file passed every check, so nothing was exported.") }
            let stamp = Date.now.formatted(.iso8601.year().month().day().dateSeparator(.dash))
            let target = try storage.publish(files: released, summary: response.summary.map(URL.init(fileURLWithPath:)),
                                             to: folder, named: "DocSan export \(stamp)")
            try? FileManager.default.removeItem(at: outbox)
            self.exported = ExportResult(folder: target, files: released.map { $0.url.lastPathComponent })
        }
    }

    func reset() {
        step = .drop
        items = []
        inputs = []
        queue = []
        resolved = []
        choices = [:]
        marks = [:]
        imageChoices = [:]
        focused = nil
        exported = nil
        selectedFile = nil
        selectedPicture = nil
        searchText = ""
    }

    private func perform(_ label: String, _ work: () async throws -> Void) async {
        busy = label
        defer { busy = nil }
        do {
            try await work()
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
