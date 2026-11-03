// PDF files, read with Apple's own PDF parser. PDFKit draws each page as a
// viewer shows it (with the page's rotation, crop box, optional content and
// annotation appearances) and reads its text layer with the box of every
// character; Core Graphics walks the document's structure so the engine can
// account for every part. The helper reports what is there; the engine
// decides what each part means (docsan.pdf.read).
//
//   {"command": "pdf-read", "input": PDF, "output": DIR, "dpi": 200, "max_pixels": 36000000,
//    "ocr_dpi": 400, "max_ocr_pixels": 100000000}
//     -> {"structure": {...}, "pages": [{"image": PNG, "width", "height", "points": [w, h],
//         "rotation", "lines": [{"text", "box", "chars": [[x, y, w, h] | null], "ink": [0...1]}],
//         "ocr_image": PNG, "ocr_width", "ocr_height"}]}
//     "ocr_image" is the page drawn again at "ocr_dpi", for reading only.
//   {"command": "pdf-text", "input": PDF} -> {"pages": ["text of page 1", ...]}
//
// Boxes are pixels of the rendered page, origin top left. "ink" is, per
// character, the share of pixels in its box that differ from the box's
// commonest brightness by more than 10 of 255 levels: text drawn in a hidden
// layer, in white on white or under an opaque box has none, while a light
// grey watermark still has some.

import CoreGraphics
import Foundation
import ImageIO
import PDFKit
import UniformTypeIdentifiers

// MARK: - Structure

/// The keys of every kind of dictionary the engine has rules for, gathered
/// across the whole document, with counts of the parts it reports.
private final class Inventory {
    var catalog = Set<String>()
    var names = Set<String>()
    var acroForm = Set<String>()
    var pageTree = Set<String>()
    var page = Set<String>()
    var resources = Set<String>()
    var image = Set<String>()
    var form = Set<String>()
    var annotations: [String: Int] = [:]
    var xobjects: [String: Int] = [:]
    var fields = 0
    var embeddedFiles = 0
    var javascript = 0
    var outline = 0
    var optionalContent = false
    var info = Set<String>()
    private var seen = Set<String>()

    /// Whether this walk meets the dictionary for the first time; a shared
    /// resource is counted once, and a loop in the file cannot recur forever.
    func first(_ d: CGPDFDictionaryRef, _ walk: String = "") -> Bool {
        seen.insert("\(walk):\(unsafeBitCast(d, to: Int.self))").inserted
    }

    var json: [String: Any] {
        ["catalog": catalog.sorted(), "names": names.sorted(), "acroform": acroForm.sorted(), "page_tree": pageTree.sorted(),
         "page": page.sorted(), "resources": resources.sorted(), "image": image.sorted(), "form": form.sorted(),
         "annotations": annotations, "xobjects": xobjects, "fields": fields, "embedded_files": embeddedFiles,
         "javascript": javascript, "outline": outline, "optional_content": optionalContent, "info": info.sorted()]
    }
}

private func keys(_ d: CGPDFDictionaryRef) -> Set<String> {
    var out = Set<String>()
    CGPDFDictionaryApplyBlock(d, { key, _, _ in
        out.insert(String(cString: key))
        return true
    }, nil)
    return out
}

private func dictionary(_ d: CGPDFDictionaryRef, _ key: String) -> CGPDFDictionaryRef? {
    var out: CGPDFDictionaryRef?
    if CGPDFDictionaryGetDictionary(d, key, &out) { return out }
    var stream: CGPDFStreamRef?
    if CGPDFDictionaryGetStream(d, key, &stream), let stream { return CGPDFStreamGetDictionary(stream) }
    return nil
}

private func array(_ d: CGPDFDictionaryRef, _ key: String) -> CGPDFArrayRef? {
    var out: CGPDFArrayRef?
    return CGPDFDictionaryGetArray(d, key, &out) ? out : nil
}

private func name(_ d: CGPDFDictionaryRef, _ key: String) -> String? {
    var out: UnsafePointer<CChar>?
    guard CGPDFDictionaryGetName(d, key, &out), let out else { return nil }
    return String(cString: out)
}

private func element(_ a: CGPDFArrayRef, _ i: Int) -> CGPDFDictionaryRef? {
    var out: CGPDFDictionaryRef?
    if CGPDFArrayGetDictionary(a, i, &out) { return out }
    var stream: CGPDFStreamRef?
    if CGPDFArrayGetStream(a, i, &stream), let stream { return CGPDFStreamGetDictionary(stream) }
    return nil
}

/// Every dictionary or stream value of a dictionary, such as each XObject in /XObject.
private func values(_ d: CGPDFDictionaryRef) -> [CGPDFDictionaryRef] {
    var out: [CGPDFDictionaryRef] = []
    CGPDFDictionaryApplyBlock(d, { _, object, _ in
        var dict: CGPDFDictionaryRef?
        var stream: CGPDFStreamRef?
        if CGPDFObjectGetValue(object, .dictionary, &dict), let dict {
            out.append(dict)
        } else if CGPDFObjectGetValue(object, .stream, &stream), let stream, let sd = CGPDFStreamGetDictionary(stream) {
            out.append(sd)
        }
        return true
    }, nil)
    return out
}

/// Entries of a name tree (/Names [key value ...] with /Kids), counted.
private func nameTreeCount(_ node: CGPDFDictionaryRef, _ inv: Inventory, depth: Int = 0) -> Int {
    guard depth < 32, inv.first(node, "names") else { return 0 }
    var n = 0
    if let names = array(node, "Names") { n += CGPDFArrayGetCount(names) / 2 }
    if let kids = array(node, "Kids") {
        for i in 0..<CGPDFArrayGetCount(kids) {
            if let kid = element(kids, i) { n += nameTreeCount(kid, inv, depth: depth + 1) }
        }
    }
    return n
}

private func outlineCount(_ item: CGPDFDictionaryRef, _ inv: Inventory, depth: Int = 0) -> Int {
    var n = 0
    var current: CGPDFDictionaryRef? = dictionary(item, "First")
    var steps = 0
    while let node = current, steps < 100_000, inv.first(node, "outline") {
        n += 1 + (depth < 64 ? outlineCount(node, inv, depth: depth + 1) : 0)
        current = dictionary(node, "Next")
        steps += 1
    }
    return n
}

/// Resources and everything drawn through them: forms, patterns, Type 3 fonts.
private func walkResources(_ r: CGPDFDictionaryRef, _ inv: Inventory, depth: Int = 0) {
    guard depth < 32, inv.first(r) else { return }
    inv.resources.formUnion(keys(r))
    if let xobjects = dictionary(r, "XObject") {
        for x in values(xobjects) {
            guard inv.first(x) else { continue }
            var kind = name(x, "Subtype") ?? "?"
            if kind == "Form", name(x, "Subtype2") == "PS" { kind = "PS" }
            inv.xobjects[kind, default: 0] += 1
            if kind == "Image" { inv.image.formUnion(keys(x)) }
            if kind == "Form" {
                inv.form.formUnion(keys(x))
                if let inner = dictionary(x, "Resources") { walkResources(inner, inv, depth: depth + 1) }
            }
        }
    }
    if let patterns = dictionary(r, "Pattern") {
        for p in values(patterns) {
            if let inner = dictionary(p, "Resources") { walkResources(inner, inv, depth: depth + 1) }
        }
    }
    if let fonts = dictionary(r, "Font") {
        for f in values(fonts) {
            if let inner = dictionary(f, "Resources") { walkResources(inner, inv, depth: depth + 1) }
        }
    }
}

/// The dictionary of every stream value of a dictionary, such as each state of an appearance.
private func streamValues(_ d: CGPDFDictionaryRef) -> [CGPDFDictionaryRef] {
    var out: [CGPDFDictionaryRef] = []
    CGPDFDictionaryApplyBlock(d, { _, object, _ in
        var stream: CGPDFStreamRef?
        if CGPDFObjectGetValue(object, .stream, &stream), let stream, let sd = CGPDFStreamGetDictionary(stream) {
            out.append(sd)
        }
        return true
    }, nil)
    return out
}

/// An annotation's appearance streams: /N, /R and /D, each a form stream or a
/// dictionary of form streams, one per state. A stream is told from a set of
/// states by what it is, not by its /Subtype, which a form may leave out.
private func walkAppearance(_ annotation: CGPDFDictionaryRef, _ inv: Inventory) {
    guard let ap = dictionary(annotation, "AP") else { return }
    for key in ["N", "R", "D"] {
        var stream: CGPDFStreamRef?
        var states: CGPDFDictionaryRef?
        var forms: [CGPDFDictionaryRef] = []
        if CGPDFDictionaryGetStream(ap, key, &stream), let stream, let form = CGPDFStreamGetDictionary(stream) {
            forms = [form]
        } else if CGPDFDictionaryGetDictionary(ap, key, &states), let states {
            forms = streamValues(states)
        }
        for form in forms where inv.first(form) {
            inv.form.formUnion(keys(form))
            if let r = dictionary(form, "Resources") { walkResources(r, inv) }
        }
    }
}

private func walkPageTree(_ node: CGPDFDictionaryRef, _ inv: Inventory, depth: Int = 0) {
    guard depth < 64, inv.first(node) else { return }
    if name(node, "Type") == "Page" || array(node, "Kids") == nil {
        inv.page.formUnion(keys(node))
    } else {
        inv.pageTree.formUnion(keys(node))
    }
    if let r = dictionary(node, "Resources") { walkResources(r, inv) }
    if let annots = array(node, "Annots") {
        for i in 0..<CGPDFArrayGetCount(annots) {
            guard let a = element(annots, i), inv.first(a) else { continue }
            inv.annotations[name(a, "Subtype") ?? "?", default: 0] += 1
            walkAppearance(a, inv)
        }
    }
    if let kids = array(node, "Kids") {
        for i in 0..<CGPDFArrayGetCount(kids) {
            if let kid = element(kids, i) { walkPageTree(kid, inv, depth: depth + 1) }
        }
    }
}

private func countFields(_ fields: CGPDFArrayRef, _ inv: Inventory, depth: Int = 0) -> Int {
    var n = 0
    for i in 0..<CGPDFArrayGetCount(fields) {
        guard let f = element(fields, i), inv.first(f, "fields") else { continue }
        n += 1
        if depth < 32, let kids = array(f, "Kids") { n += countFields(kids, inv, depth: depth + 1) }
    }
    return n
}

private func inventory(_ document: CGPDFDocument) -> [String: Any] {
    let inv = Inventory()
    if let info = document.info { inv.info = keys(info) }
    guard let catalog = document.catalog else { return inv.json }
    inv.catalog = keys(catalog)
    _ = inv.first(catalog)
    if let names = dictionary(catalog, "Names") {
        inv.names = keys(names)
        if let files = dictionary(names, "EmbeddedFiles") { inv.embeddedFiles = nameTreeCount(files, inv) }
        if let scripts = dictionary(names, "JavaScript") { inv.javascript = nameTreeCount(scripts, inv) }
    }
    if let form = dictionary(catalog, "AcroForm") {
        inv.acroForm = keys(form)
        if let fields = array(form, "Fields") { inv.fields = countFields(fields, inv) }
    }
    if let outlines = dictionary(catalog, "Outlines") { inv.outline = outlineCount(outlines, inv) }
    inv.optionalContent = dictionary(catalog, "OCProperties") != nil
    if let pages = dictionary(catalog, "Pages") { walkPageTree(pages, inv) }
    return inv.json
}

// MARK: - Pages

private func pixelBox(_ r: CGRect, _ transform: CGAffineTransform, scale: Double, height: Int) -> [Double]? {
    guard r.width > 0 || r.height > 0 else { return nil }
    let t = r.applying(transform)
    let box = [t.minX * scale, Double(height) - t.maxY * scale, t.width * scale, t.height * scale]
    return box.map { ($0 * 10).rounded() / 10 }
}

/// Share of pixels in the box that differ clearly from the box's commonest brightness.
private func ink(_ ctx: CGContext, _ b: [Double]) -> Double {
    guard let base = ctx.data else { return 0 }
    let px = base.assumingMemoryBound(to: UInt8.self)
    let stride = ctx.bytesPerRow, w = ctx.width, h = ctx.height
    let x0 = max(0, Int(floor(b[0]))), y0 = max(0, Int(floor(b[1])))
    let x1 = min(w - 1, Int(ceil(b[0] + b[2]))), y1 = min(h - 1, Int(ceil(b[1] + b[3])))
    guard x1 >= x0, y1 >= y0 else { return 0 }
    var histogram = [Int](repeating: 0, count: 32)
    var lumas: [Int] = []
    lumas.reserveCapacity((x1 - x0 + 1) * (y1 - y0 + 1))
    for y in y0...y1 {
        for x in x0...x1 {
            let i = y * stride + x * 4
            let l = (Int(px[i]) * 3 + Int(px[i + 1]) * 6 + Int(px[i + 2])) / 10
            lumas.append(l)
            histogram[l / 8] += 1
        }
    }
    let background = (histogram.indices.max { histogram[$0] < histogram[$1] } ?? 31) * 8 + 4
    let differing = lumas.filter { abs($0 - background) > 10 }.count
    return (Double(differing) / Double(lumas.count) * 1000).rounded() / 1000
}

/// The page drawn on white at `scale`, as a viewer shows it: PDFKit applies the
/// page's rotation and crop box, draws only the layers the document shows by
/// default, and draws visible annotations.
private func draw(_ page: PDFPage, number: Int, scale: Double, size: CGSize) throws -> CGContext {
    let w = Int((size.width * scale).rounded()), h = Int((size.height * scale).rounded())
    guard w > 0, h > 0, w * h <= maxPixels, let srgb = CGColorSpace(name: CGColorSpace.sRGB),
          let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
                              space: srgb, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)
    else { throw Failure(message: "page \(number) is too large") }
    ctx.setFillColor(CGColor(srgbRed: 1, green: 1, blue: 1, alpha: 1))
    ctx.fill(CGRect(x: 0, y: 0, width: w, height: h))
    ctx.interpolationQuality = .high
    ctx.scaleBy(x: scale, y: scale)
    page.draw(with: .cropBox, to: ctx)
    return ctx
}

private func writePNG(_ ctx: CGContext, to output: URL, number: Int) throws {
    guard let image = ctx.makeImage(),
          let dest = CGImageDestinationCreateWithURL(output as CFURL, UTType.png.identifier as CFString, 1, nil)
    else { throw Failure(message: "cannot write page \(number)") }
    CGImageDestinationAddImage(dest, image, nil)
    guard CGImageDestinationFinalize(dest) else { throw Failure(message: "cannot write page \(number)") }
}

private func readPage(_ page: PDFPage, number: Int, output: URL, dpi: Double, maxPagePixels: Double,
                      ocrDPI: Double?, maxOCRPixels: Double) throws -> [String: Any] {
    let crop = page.bounds(for: .cropBox)
    let transform = page.transform(for: .cropBox)
    let shown = crop.applying(transform)
    guard shown.width >= 1, shown.height >= 1 else { throw Failure(message: "page \(number) has no visible area") }
    // A large page is drawn at a lower resolution, so every page fits the limit.
    let scale = min(dpi / 72, (maxPagePixels / Double(shown.width * shown.height)).squareRoot())
    let ctx = try draw(page, number: number, scale: scale, size: shown.size)
    let w = ctx.width, h = ctx.height

    var lines: [[String: Any]] = []
    let text = (page.string ?? "") as NSString
    var current = ""
    var chars: [Any] = []
    var inks: [Double] = []
    func flush() {
        let boxes = chars.compactMap { $0 as? [Double] }
        if !current.trimmingCharacters(in: .whitespaces).isEmpty, !boxes.isEmpty {
            let x0 = boxes.map { $0[0] }.min()!, y0 = boxes.map { $0[1] }.min()!
            let x1 = boxes.map { $0[0] + $0[2] }.max()!, y1 = boxes.map { $0[1] + $0[3] }.max()!
            lines.append(["text": current, "box": [x0, y0, x1 - x0, y1 - y0], "chars": chars, "ink": inks])
        }
        current = ""
        chars = []
        inks = []
    }
    var offset = 0
    for scalar in (text as String).unicodeScalars {
        let length = scalar.utf16.count
        defer { offset += length }
        if scalar == "\n" || scalar == "\r" || scalar == "\u{2028}" || scalar == "\u{2029}" {
            flush()
            continue
        }
        current.unicodeScalars.append(scalar)
        // A selection of the one character, not characterBounds(at:): that
        // index drifts from the string after each line break PDFKit adds.
        let selection = page.selection(for: NSRange(location: offset, length: length))
        if let selection, selection.string == String(scalar),
           let box = pixelBox(selection.bounds(for: page), transform, scale: scale, height: h) {
            chars.append(box)
            inks.append(ink(ctx, box))
        } else {
            chars.append(NSNull())
            inks.append(0)
        }
    }
    flush()

    try writePNG(ctx, to: output, number: number)
    var answer: [String: Any] = ["image": output.path, "width": w, "height": h,
                                 "points": [Double(shown.width), Double(shown.height)], "rotation": page.rotation, "lines": lines]
    // A sharper drawing for reading only: labels a few pixels high in the page
    // picture are legible to OCR at twice the resolution.
    if let ocrDPI {
        let ocrScale = min(ocrDPI / 72, (maxOCRPixels / Double(shown.width * shown.height)).squareRoot())
        if ocrScale > scale * 1.01 {
            let sharp = try draw(page, number: number, scale: ocrScale, size: shown.size)
            let ocrOutput = output.deletingPathExtension().appendingPathExtension("ocr.png")
            try writePNG(sharp, to: ocrOutput, number: number)
            answer["ocr_image"] = ocrOutput.path
            answer["ocr_width"] = sharp.width
            answer["ocr_height"] = sharp.height
        }
    }
    return answer
}

func readPDF(_ input: String, output: String, dpi: Double, maxPagePixels: Double, ocrDPI: Double? = nil,
             maxOCRPixels: Double = 100_000_000) throws -> [String: Any] {
    let url = URL(fileURLWithPath: input)
    guard let document = PDFDocument(url: url), let core = document.documentRef else {
        throw Failure(message: "cannot open the PDF")
    }
    var structure = inventory(core)
    structure["encrypted"] = document.isEncrypted
    structure["version"] = "\(document.majorVersion).\(document.minorVersion)"
    structure["page_count"] = document.pageCount
    if document.isEncrypted || document.isLocked {
        return ["structure": structure, "pages": []]
    }
    let folder = URL(fileURLWithPath: output, isDirectory: true)
    try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    var pages: [[String: Any]] = []
    for i in 0..<document.pageCount {
        guard let page = document.page(at: i) else { throw Failure(message: "cannot read page \(i + 1)") }
        try autoreleasepool {
            pages.append(try readPage(page, number: i + 1, output: folder.appendingPathComponent("page-\(i + 1).png"),
                                      dpi: dpi, maxPagePixels: maxPagePixels, ocrDPI: ocrDPI, maxOCRPixels: maxOCRPixels))
        }
    }
    return ["structure": structure, "pages": pages]
}

/// Each page's text as PDFKit extracts it: the verifier's second extractor.
func pdfText(_ input: String) throws -> [String: Any] {
    guard let document = PDFDocument(url: URL(fileURLWithPath: input)) else { throw Failure(message: "cannot open the PDF") }
    if document.isLocked { throw Failure(message: "the PDF is locked") }
    let pages = (0..<document.pageCount).map { document.page(at: $0)?.string ?? "" }
    return ["pages": pages, "encrypted": document.isEncrypted]
}
