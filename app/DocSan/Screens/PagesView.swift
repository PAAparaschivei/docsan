import AppKit
import SwiftUI

// The document as pages, laid out by the macOS text system from the engine's
// description (`docsan.docx.pages`): paragraphs with their fonts, spacing and
// tab stops, list numbers, tables with borders and shading, pictures,
// headers, footers and page numbers. It runs in the app's process, inside the
// sandbox and without network access, which a web view could not.

// MARK: - The description

private typealias JSON = [String: Any]

private extension Dictionary where Key == String, Value == Any {
    func num(_ key: String, _ fallback: Double = 0) -> Double { (self[key] as? NSNumber)?.doubleValue ?? fallback }
    func str(_ key: String) -> String? { self[key] as? String }
    func bool(_ key: String) -> Bool { (self[key] as? Bool) ?? false }
    func dict(_ key: String) -> JSON? { self[key] as? JSON }
    func list(_ key: String) -> [JSON] { self[key] as? [JSON] ?? [] }
}

private func color(_ hex: String?) -> NSColor? {
    guard let hex, hex.count == 6, let v = Int(hex, radix: 16) else { return nil }
    return NSColor(srgbRed: CGFloat(v >> 16 & 0xFF) / 255, green: CGFloat(v >> 8 & 0xFF) / 255, blue: CGFloat(v & 0xFF) / 255, alpha: 1)
}

private let leaderKey = NSAttributedString.Key("DocSanLeader")
private let fieldKey = NSAttributedString.Key("DocSanField")

// MARK: - Building text

private struct Drawing {
    let item: JSON
    let paragraph: Int  // character index of the paragraph that holds it
}

private final class TextBuilder {
    let folder: URL
    let width: CGFloat  // the text column
    let text = NSMutableAttributedString()
    var drawings: [Drawing] = []

    init(folder: URL, width: CGFloat) {
        self.folder = folder
        self.width = width
    }

    static let serif: Set<String> = ["cambria", "times new roman", "times", "georgia", "garamond", "book antiqua", "palatino",
                                     "palatino linotype", "baskerville", "century", "constantia", "bookman old style"]
    static let mono: Set<String> = ["consolas", "courier new", "courier", "menlo", "monaco", "lucida console"]

    func font(_ s: JSON, scale: CGFloat = 1) -> NSFont {
        let name = s.str("font") ?? "Times New Roman"
        let size = CGFloat(s.num("size", 10)) * scale
        var traits: NSFontTraitMask = []
        if s.bool("b") { traits.insert(.boldFontMask) }
        if s.bool("i") { traits.insert(.italicFontMask) }
        let key = name.lowercased()
        let fallback = Self.serif.contains(key) ? ["Times New Roman", "Georgia"] : Self.mono.contains(key) ? ["Menlo", "Courier"] : ["Helvetica Neue", "Helvetica"]
        for family in [name] + fallback {
            if let f = NSFontManager.shared.font(withFamily: family, traits: traits, weight: 5, size: size) { return f }
        }
        return NSFont.systemFont(ofSize: size)
    }

    func attributes(_ s: JSON, paragraph: NSParagraphStyle) -> [NSAttributedString.Key: Any] {
        let raised = s.str("va") != nil
        var a: [NSAttributedString.Key: Any] = [
            .font: font(s, scale: raised ? 0.65 : 1),
            .foregroundColor: color(s.str("color")) ?? NSColor.black,
            .paragraphStyle: paragraph,
        ]
        let size = CGFloat(s.num("size", 10))
        if s.str("va") == "super" { a[.baselineOffset] = size * 0.33 }
        if s.str("va") == "sub" { a[.baselineOffset] = -size * 0.15 }
        if s["rise"] != nil { a[.baselineOffset] = CGFloat(s.num("rise")) }
        if let u = s.str("u") {
            a[.underlineStyle] = (u == "double" ? NSUnderlineStyle.double : u.contains("thick") || u.contains("Heavy") ? .thick : .single).rawValue
        }
        if s.bool("strike") { a[.strikethroughStyle] = NSUnderlineStyle.single.rawValue }
        if let bg = color(s.str("bg")) { a[.backgroundColor] = bg }
        if s["spacing"] != nil { a[.kern] = CGFloat(s.num("spacing")) }
        return a
    }

    func border(_ block: NSTextBlock, _ b: JSON?, _ edge: NSRectEdge) {
        guard let b else { return }
        block.setWidth(CGFloat(max(b.num("w", 0.5), 0.5)), type: .absoluteValueType, for: .border, edge: edge)
        block.setBorderColor(color(b.str("color")) ?? .black, for: edge)
    }

    func paragraphStyle(_ p: JSON, blocks: [NSTextBlock]) -> NSMutableParagraphStyle {
        let style = NSMutableParagraphStyle()
        // In a table cell a justified line is often a single word, which the text
        // system would spread letter by letter and Word does not.
        let justify: NSTextAlignment = blocks.contains { $0 is NSTextTableBlock } ? .left : .justified
        style.alignment = ["center": .center, "right": .right, "justify": justify][p.str("align") ?? ""] ?? .left
        let blocked = p.str("fill") != nil || p.dict("borders") != nil
        let left = blocked ? 0 : CGFloat(p.num("left")), first = CGFloat(p.num("first"))
        style.headIndent = left
        style.firstLineHeadIndent = max(left + first, 0)
        style.tailIndent = -CGFloat(p.num("right"))
        style.paragraphSpacingBefore = CGFloat(p.num("before"))
        style.paragraphSpacing = CGFloat(p.num("after"))
        if let line = p.dict("line") {
            let value = CGFloat(line.num("value", 1))
            switch line.str("rule") {
            case "exact": style.minimumLineHeight = value; style.maximumLineHeight = value
            case "atLeast": style.minimumLineHeight = value
            default: style.lineHeightMultiple = value
            }
        }
        // Word lets a tab stop lie past the right indent and stops the text at the indent;
        // the text system would wrap the line, so the stop is brought inside.
        let lineEnd = max(width - CGFloat(p.num("right")) - 0.5, 1)
        var tabs = p.list("tabs").map { t -> NSTextTab in
            let align: NSTextAlignment = ["center": .center, "right": .right, "decimal": .right][t.str("align") ?? ""] ?? .left
            return NSTextTab(textAlignment: align, location: min(CGFloat(t.num("pos")), lineEnd))
        }
        if p.dict("label") != nil, first < 0 { tabs.append(NSTextTab(textAlignment: .left, location: left)) }  // after the number
        style.tabStops = tabs.sorted { $0.location < $1.location }
        style.defaultTabInterval = 36
        var all = blocks
        if p.str("fill") != nil || p.dict("borders") != nil {
            // A shaded or bordered paragraph: a block across its indented width.
            let block = NSTextBlock()
            block.setValue(max(width - CGFloat(p.num("left")) - CGFloat(p.num("right")), 10), type: .absoluteValueType, for: .width)
            block.setWidth(CGFloat(p.num("left")), type: .absoluteValueType, for: .margin, edge: .minX)
            block.backgroundColor = color(p.str("fill"))
            for (side, edge) in [("top", NSRectEdge.minY), ("bottom", .maxY), ("left", .minX), ("right", .maxX)] {
                if let b = p.dict("borders")?.dict(side) {
                    border(block, b, edge)
                    block.setWidth(CGFloat(b.num("space")), type: .absoluteValueType, for: .padding, edge: edge)
                }
            }
            all.append(block)
        }
        style.textBlocks = all
        return style
    }

    func paragraph(_ p: JSON, blocks: [NSTextBlock]) {
        let style = paragraphStyle(p, blocks: blocks)
        let start = text.length
        if p.bool("page_break"), start > 0 { text.append(NSAttributedString(string: "\u{000C}", attributes: [.paragraphStyle: style])) }
        if let label = p.dict("label") {
            text.append(NSAttributedString(string: label.str("text") ?? "", attributes: attributes(label, paragraph: style)))
            let gap = label.str("suffix") == "space" ? " " : label.str("suffix") == "nothing" ? "" : "\t"
            text.append(NSAttributedString(string: gap, attributes: attributes(label, paragraph: style)))
        }
        for d in p.list("drawings") {
            if d.dict("anchor") != nil {
                drawings.append(Drawing(item: d, paragraph: start))
            } else if d.str("kind") == "image" {
                text.append(picture(d, paragraph: style))
            }
        }
        let leader = p.list("tabs").last.flatMap { $0.str("leader") }.flatMap { $0 == "none" ? nil : $0 }
        for run in p.list("runs") {
            var a = attributes(run, paragraph: style)
            if let field = run.str("field") { a[fieldKey] = field }
            var s = run.str("text") ?? ""
            if run.bool("caps") { s = s.uppercased() }
            // Breaks and positional tabs as the text system knows them.
            s = s.replacingOccurrences(of: "\n", with: "\u{2028}").replacingOccurrences(of: "\u{1d}", with: "\u{2028}")
            for c in ["\u{1c}", "\u{1e}", "\u{1f}"] { s = s.replacingOccurrences(of: c, with: "\t") }
            let piece = NSMutableAttributedString(string: s, attributes: a)
            if let leader {
                let ns = s as NSString
                var r = ns.range(of: "\t")
                while r.location != NSNotFound {
                    piece.addAttribute(leaderKey, value: leader, range: r)
                    r = ns.range(of: "\t", range: NSRange(location: NSMaxRange(r), length: ns.length - NSMaxRange(r)))
                }
            }
            text.append(piece)
        }
        if text.length == start || (p.list("runs").isEmpty && p.dict("label") == nil) {
            text.append(NSAttributedString(string: "\u{200B}", attributes: attributes(p.dict("mark") ?? [:], paragraph: style)))
        }
        text.append(NSAttributedString(string: "\n", attributes: attributes(p.dict("mark") ?? [:], paragraph: style)))
    }

    func picture(_ d: JSON, paragraph: NSParagraphStyle) -> NSAttributedString {
        let attachment = NSTextAttachment()
        let size = CGSize(width: d.num("w", 216), height: d.num("h", 144))
        if let file = d.str("file"), let image = NSImage(contentsOf: folder.appendingPathComponent(file)) {
            attachment.image = image
        } else {
            let placeholder = NSImage(size: size)
            placeholder.lockFocus()
            NSColor(white: 0.9, alpha: 1).setFill()
            NSBezierPath(rect: CGRect(origin: .zero, size: size)).fill()
            placeholder.unlockFocus()
            attachment.image = placeholder
        }
        attachment.bounds = CGRect(origin: .zero, size: size)
        let s = NSMutableAttributedString(attachment: attachment)
        s.addAttribute(.paragraphStyle, value: paragraph, range: NSRange(location: 0, length: s.length))
        return s
    }

    func table(_ t: JSON, blocks: [NSTextBlock]) {
        let table = NSTextTable()
        let count = max(Int(t.num("count", 1)), 1)
        table.numberOfColumns = count
        table.collapsesBorders = true
        table.hidesEmptyCells = false
        let columns = (t["columns"] as? [NSNumber])?.map { CGFloat(truncating: $0) } ?? []
        if columns.count == count {
            table.layoutAlgorithm = .fixedLayoutAlgorithm
            let total = columns.reduce(0, +)
            table.setValue(min(total, width), type: .absoluteValueType, for: .width)
        } else if let w = t.dict("width") {
            if w["pt"] != nil { table.setValue(CGFloat(w.num("pt")), type: .absoluteValueType, for: .width) }
            if w["pct"] != nil { table.setValue(CGFloat(w.num("pct")), type: .percentageValueType, for: .width) }
        }
        let indent = CGFloat(t.num("indent"))
        if t.str("align") == "center", columns.count == count {
            table.setWidth(max((width - columns.reduce(0, +)) / 2, 0), type: .absoluteValueType, for: .margin, edge: .minX)
        } else if indent != 0 {
            table.setWidth(indent, type: .absoluteValueType, for: .margin, edge: .minX)
        }
        for (r, row) in t.list("rows").enumerated() {
            for cell in row.list("cells") {
                let col = Int(cell.num("col")), span = max(Int(cell.num("span", 1)), 1)
                let block = NSTextTableBlock(table: table, startingRow: r, rowSpan: max(Int(cell.num("rowspan", 1)), 1),
                                             startingColumn: min(col, count - 1), columnSpan: min(span, count - col))
                if columns.count == count {
                    let w = columns[min(col, count - 1)..<min(col + span, count)].reduce(0, +)
                    block.setValue(w, type: .absoluteValueType, for: .width)
                }
                block.backgroundColor = color(cell.str("fill"))
                let pad = (cell["pad"] as? [NSNumber])?.map { CGFloat(truncating: $0) } ?? [0, 5.4, 0, 5.4]
                for (i, edge) in [NSRectEdge.minY, .maxX, .maxY, .minX].enumerated() where i < pad.count {
                    block.setWidth(pad[i], type: .absoluteValueType, for: .padding, edge: edge)
                }
                let borders = cell.dict("borders")
                for (side, edge) in [("top", NSRectEdge.minY), ("bottom", .maxY), ("left", .minX), ("right", .maxX)] {
                    border(block, borders?.dict(side), edge)
                }
                block.verticalAlignment = ["center": .middleAlignment, "bottom": .bottomAlignment][cell.str("valign") ?? ""] ?? .topAlignment
                if let h = row["height"] as? NSNumber { block.setValue(CGFloat(truncating: h), type: .absoluteValueType, for: .minimumHeight) }
                let inner = cell.list("blocks")
                if inner.isEmpty {
                    paragraph(["t": "p", "runs": []], blocks: blocks + [block])
                } else {
                    self.blocks(inner, blocks: blocks + [block])
                }
            }
        }
    }

    func blocks(_ items: [JSON], blocks: [NSTextBlock] = []) {
        for item in items {
            if item.str("t") == "table" { table(item, blocks: blocks) } else { paragraph(item, blocks: blocks) }
        }
    }
}

/// Draws the dotted or dashed leaders that fill a tab, which the text system does not.
private final class LeaderLayoutManager: NSLayoutManager {
    override func drawGlyphs(forGlyphRange glyphsToShow: NSRange, at origin: NSPoint) {
        super.drawGlyphs(forGlyphRange: glyphsToShow, at: origin)
        guard let storage = textStorage else { return }
        let chars = characterRange(forGlyphRange: glyphsToShow, actualGlyphRange: nil)
        storage.enumerateAttribute(leaderKey, in: chars) { value, range, _ in
            guard let leader = value as? String else { return }
            for i in range.location..<NSMaxRange(range) {
                let glyphs = glyphRange(forCharacterRange: NSRange(location: i, length: 1), actualCharacterRange: nil)
                guard let container = textContainer(forGlyphAt: glyphs.location, effectiveRange: nil) else { continue }
                var rect = boundingRect(forGlyphRange: glyphs, in: container)
                rect.origin.x += origin.x
                rect.origin.y += origin.y
                let font = storage.attribute(.font, at: i, effectiveRange: nil) as? NSFont
                let y = rect.minY + (font.map { $0.ascender } ?? rect.height * 0.8)
                let path = NSBezierPath()
                path.move(to: CGPoint(x: rect.minX + 2, y: y))
                path.line(to: CGPoint(x: rect.maxX - 2, y: y))
                path.lineWidth = leader == "heavy" ? 1.2 : 0.8
                if leader == "dot" || leader == "middleDot" { path.setLineDash([0.8, 2.4], count: 2, phase: 0); path.lineCapStyle = .round }
                if leader == "hyphen" { path.setLineDash([3, 2], count: 2, phase: 0) }
                (storage.attribute(.foregroundColor, at: i, effectiveRange: nil) as? NSColor ?? .black).setStroke()
                path.stroke()
            }
        }
    }
}

// MARK: - Pages

private class FlippedView: NSView {
    override var isFlipped: Bool { true }
}

private final class PageView: FlippedView {
    override func draw(_ dirtyRect: NSRect) {
        NSColor.white.setFill()
        bounds.intersection(dirtyRect).fill()  // snapshots pass an unclipped rect
    }
}

/// A text box: its fill and outline, drawn rather than layer-backed so snapshots include it.
private final class BoxView: FlippedView {
    var fill: NSColor?
    var line: NSColor?
    var lineWidth: CGFloat = 0.75
    var radius: CGFloat = 0

    override func draw(_ dirtyRect: NSRect) {
        let path = NSBezierPath(roundedRect: bounds.insetBy(dx: lineWidth / 2, dy: lineWidth / 2), xRadius: radius, yRadius: radius)
        if let fill { fill.setFill(); path.fill() }
        if let line { line.setStroke(); path.lineWidth = lineWidth; path.stroke() }
    }
}

/// Lays out a whole description and builds one view per page.
private final class PagesDocumentView: FlippedView {
    private var managers: [NSLayoutManager] = []  // the text views do not keep their layout managers

    override func draw(_ dirtyRect: NSRect) {
        // Each page's shadow; the pages draw themselves on top.
        let shadow = NSShadow()
        shadow.shadowColor = NSColor.black.withAlphaComponent(0.22)
        shadow.shadowBlurRadius = 8
        shadow.shadowOffset = NSSize(width: 0, height: -2)
        NSGraphicsContext.saveGraphicsState()
        shadow.set()
        NSColor.white.setFill()
        for page in subviews where page is PageView { page.frame.fill() }
        NSGraphicsContext.restoreGraphicsState()
    }

    func load(_ model: JSON, folder: URL) {
        subviews.forEach { $0.removeFromSuperview() }
        managers = []
        let evenOdd = model.bool("even_odd")
        var pages: [(view: NSView, section: JSON, first: Bool)] = []
        var anchored: [(first: Int, page: JSON, drawing: Drawing, manager: NSLayoutManager, containers: [NSTextContainer])] = []
        let gap: CGFloat = 24
        var y: CGFloat = gap
        var widest: CGFloat = 0
        for section in model.list("sections") {
            var page = section.dict("page") ?? [:]
            let sectionStart = pages.count
            let size = CGSize(width: page.num("w", 595), height: page.num("h", 842))
            // Word starts the body below a header taller than the top margin, and ends it above a tall footer.
            let textWidth = size.width - page.num("ml", 72) - page.num("mr", 72)
            let header = measure((section.dict("headers")?["default"] as? [JSON]) ?? [], width: textWidth, folder: folder)
            let footer = measure((section.dict("footers")?["default"] as? [JSON]) ?? [], width: textWidth, folder: folder)
            page["mt"] = max(page.num("mt", 72), page.num("hd", 35) + header)
            page["mb"] = max(page.num("mb", 72), page.num("ft", 35) + footer)
            let content = CGSize(width: textWidth, height: size.height - page.num("mt", 72) - page.num("mb", 72))
            let builder = TextBuilder(folder: folder, width: content.width)
            builder.blocks(section.list("blocks"))
            let storage = NSTextStorage(attributedString: builder.text)
            let manager = LeaderLayoutManager()
            storage.addLayoutManager(manager)
            managers.append(manager)
            var containers: [NSTextContainer] = []
            while containers.count < 400 {
                let container = NSTextContainer(size: content)
                container.lineFragmentPadding = 0
                container.widthTracksTextView = false
                container.heightTracksTextView = false
                manager.addTextContainer(container)
                containers.append(container)
                let range = manager.glyphRange(for: container)
                if NSMaxRange(range) >= manager.numberOfGlyphs || (range.length == 0 && containers.count > 1) { break }
            }
            for (i, container) in containers.enumerated() {
                let view = PageView(frame: CGRect(x: 0, y: y, width: size.width, height: size.height))
                let body = NSTextView(frame: CGRect(x: page.num("ml", 72), y: page.num("mt", 72), width: content.width, height: content.height),
                                      textContainer: container)
                body.isEditable = false
                body.drawsBackground = false
                body.textContainerInset = .zero
                body.isVerticallyResizable = false
                body.isHorizontallyResizable = false
                view.addSubview(body)
                addSubview(view)
                var adjusted = section
                adjusted["page"] = page
                pages.append((view, adjusted, i == 0))
                y += size.height + gap
                widest = max(widest, size.width)
            }
            for drawing in builder.drawings {
                anchored.append((sectionStart, page, drawing, manager, containers))
            }
        }
        // Headers, footers and page numbers, once the page count is known.
        for (n, entry) in pages.enumerated() {
            let section = entry.section, page = section.dict("page") ?? [:]
            let number = n + 1
            let kind: String = entry.first && section.bool("title_page") ? "first" : evenOdd && number % 2 == 0 ? "even" : "default"
            for part in ["headers", "footers"] {
                let parts = section.dict(part) ?? [:]
                guard let blocks = (parts[kind] ?? (kind == "first" ? nil : parts["default"])) as? [JSON] else { continue }
                let width = entry.view.bounds.width - page.num("ml", 72) - page.num("mr", 72)
                let builder = TextBuilder(folder: folder, width: width)
                builder.blocks(blocks)
                let text = builder.text
                text.enumerateAttribute(fieldKey, in: NSRange(location: 0, length: text.length), options: .reverse) { value, range, _ in
                    guard let field = value as? String else { return }
                    let attrs = text.attributes(at: range.location, effectiveRange: nil)
                    text.replaceCharacters(in: range, with: NSAttributedString(string: field == "PAGE" ? "\(number)" : "\(pages.count)", attributes: attrs))
                }
                let storage = NSTextStorage(attributedString: text)
                let manager = LeaderLayoutManager()
                storage.addLayoutManager(manager)
                managers.append(manager)
                let container = NSTextContainer(size: CGSize(width: width, height: 10_000))
                container.lineFragmentPadding = 0
                manager.addTextContainer(container)
                let used = { manager.ensureLayout(for: container); return manager.usedRect(for: container).height }()
                let top = part == "headers" ? page.num("hd", 35) : entry.view.bounds.height - page.num("ft", 35) - used
                let view = NSTextView(frame: CGRect(x: page.num("ml", 72), y: top, width: width, height: used + 2), textContainer: container)
                view.isEditable = false
                view.drawsBackground = false
                view.textContainerInset = .zero
                entry.view.addSubview(view)
                for drawing in builder.drawings {
                    place(drawing.item, on: entry.view, page: page, paragraphTop: top, folder: folder)
                }
            }
        }
        // Floating pictures and text boxes, on the page where their paragraph landed.
        for entry in anchored {
            let glyph = entry.manager.glyphIndexForCharacter(at: min(entry.drawing.paragraph, max(entry.manager.numberOfGlyphs - 1, 0)))
            guard let container = entry.manager.textContainer(forGlyphAt: glyph, effectiveRange: nil),
                  let index = entry.containers.firstIndex(where: { $0 === container }), entry.first + index < pages.count else { continue }
            let line = entry.manager.lineFragmentRect(forGlyphAt: glyph, effectiveRange: nil)
            place(entry.drawing.item, on: pages[entry.first + index].view, page: entry.page,
                  paragraphTop: entry.page.num("mt", 72) + line.minY, folder: folder)
        }
        frame = CGRect(x: 0, y: 0, width: widest + 2 * gap, height: y)
        for entry in pages { entry.view.frame.origin.x = (frame.width - entry.view.frame.width) / 2 }
    }

    /// The height of blocks laid out at a width, such as a header.
    private func measure(_ blocks: [JSON], width: CGFloat, folder: URL) -> CGFloat {
        guard !blocks.isEmpty else { return 0 }
        let builder = TextBuilder(folder: folder, width: width)
        builder.blocks(blocks)
        let storage = NSTextStorage(attributedString: builder.text)
        let manager = NSLayoutManager()
        storage.addLayoutManager(manager)
        let container = NSTextContainer(size: CGSize(width: width, height: 10_000))
        container.lineFragmentPadding = 0
        manager.addTextContainer(container)
        return { manager.ensureLayout(for: container); return manager.usedRect(for: container).height }()
    }

    /// A floating drawing, positioned as Word positions it.
    private func place(_ item: JSON, on view: NSView, page: JSON, paragraphTop: CGFloat, folder: URL) {
        guard let anchor = item.dict("anchor") else { return }
        let w = CGFloat(item.num("w")), h = CGFloat(item.num("h"))
        let W = view.bounds.width, H = view.bounds.height
        let ml = CGFloat(page.num("ml", 72)), mr = CGFloat(page.num("mr", 72)), mt = CGFloat(page.num("mt", 72)), mb = CGFloat(page.num("mb", 72))
        let hFrame: (CGFloat, CGFloat) = ["page": (0, W), "leftMargin": (0, ml), "rightMargin": (W - mr, W)][anchor.str("hf") ?? ""] ?? (ml, W - mr)
        let vFrame: (CGFloat, CGFloat) = ["page": (0, H), "topMargin": (0, mt), "bottomMargin": (H - mb, H), "margin": (mt, H - mb)][anchor.str("vf") ?? ""]
            ?? (paragraphTop, paragraphTop + h)
        func along(_ kind: String?, _ value: String?, _ frame: (CGFloat, CGFloat), _ size: CGFloat) -> CGFloat {
            if kind == "posOffset" { return frame.0 + CGFloat(Double(value ?? "0") ?? 0) / 12700 }
            switch value {
            case "center": return (frame.0 + frame.1 - size) / 2
            case "right", "bottom", "outside": return frame.1 - size
            default: return frame.0
            }
        }
        let rect = CGRect(x: along(anchor.str("hk"), anchor.str("hv"), hFrame, w), y: along(anchor.str("vk"), anchor.str("vv"), vFrame, h), width: w, height: h)
        let shown: NSView
        if item.str("kind") == "image" {
            let image = NSImageView(frame: rect)
            image.imageScaling = .scaleAxesIndependently
            if let file = item.str("file") { image.image = NSImage(contentsOf: folder.appendingPathComponent(file)) }
            shown = image
        } else {
            let box = BoxView(frame: rect)
            box.fill = color(item.str("fill"))
            box.line = color(item.str("line"))
            box.lineWidth = CGFloat(item.num("line_w", 0.75))
            box.radius = item.str("shape") == "roundRect" ? 8 : item.str("shape") == "ellipse" ? min(w, h) / 2 : 0
            let pad = (item["pad"] as? [NSNumber])?.map { CGFloat(truncating: $0) } ?? [3.6, 7.2, 3.6, 7.2]
            let wraps = (item["wrap"] as? Bool) ?? true
            let inner = CGSize(width: wraps ? max(w - pad[1] - pad[3], 1) : 2000, height: max(h - pad[0] - pad[2], 1))
            let builder = TextBuilder(folder: folder, width: inner.width)
            builder.blocks(item.list("blocks"))
            let storage = NSTextStorage(attributedString: builder.text)
            let manager = LeaderLayoutManager()
            storage.addLayoutManager(manager)
            managers.append(manager)
            let container = NSTextContainer(size: inner)
            container.lineFragmentPadding = 0
            manager.addTextContainer(container)
            let used = { manager.ensureLayout(for: container); return manager.usedRect(for: container).height }()
            let offset = item.str("valign") == "center" ? (inner.height - used) / 2 : item.str("valign") == "bottom" ? inner.height - used : 0
            let text = NSTextView(frame: CGRect(x: pad[3], y: pad[0] + max(offset, 0), width: inner.width, height: inner.height), textContainer: container)
            text.isEditable = false
            text.drawsBackground = false
            text.textContainerInset = .zero
            box.addSubview(text)
            shown = box
        }
        if anchor.bool("behind") {
            view.addSubview(shown, positioned: .below, relativeTo: view.subviews.first)
        } else {
            view.addSubview(shown)
        }
    }
}

/// The pages in a scroll view that fits the widest page to the width.
struct PagesView: NSViewRepresentable {
    let url: URL  // pages.json, with its pictures beside it

    final class Coordinator {
        var loaded: URL?
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSScrollView()
        scroll.hasVerticalScroller = true
        scroll.hasHorizontalScroller = true
        scroll.autohidesScrollers = true
        scroll.allowsMagnification = true
        scroll.drawsBackground = false
        scroll.documentView = PagesDocumentView()
        scroll.appearance = NSAppearance(named: .aqua)
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        guard context.coordinator.loaded != url, let doc = scroll.documentView as? PagesDocumentView,
              let data = try? Data(contentsOf: url), let model = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
        context.coordinator.loaded = url
        doc.load(model, folder: url.deletingLastPathComponent())
        DispatchQueue.main.async {
            let fit = scroll.contentSize.width / max(doc.frame.width, 1)
            scroll.magnification = min(max(fit, 0.3), 1.5)
            doc.scroll(.zero)
        }
    }
}

/// The same pages drawn into an image, for the self-test.
@MainActor
func pagesSnapshot(_ url: URL, width: CGFloat = 1000) -> NSImage? {
    guard let data = try? Data(contentsOf: url), let model = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
    let doc = PagesDocumentView()
    doc.appearance = NSAppearance(named: .aqua)
    doc.load(model, folder: url.deletingLastPathComponent())
    let bounds = CGRect(x: 0, y: 0, width: doc.frame.width, height: min(doc.frame.height, 30000))
    guard let rep = doc.bitmapImageRepForCachingDisplay(in: bounds) else { return nil }
    doc.cacheDisplay(in: bounds, to: rep)
    let image = NSImage(size: bounds.size)
    image.addRepresentation(rep)
    return image
}
