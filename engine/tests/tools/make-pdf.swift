// Test-only maker of synthetic PDF fixtures, written by Quartz. Reads a JSON
// spec on stdin:
//
//   {"output": PATH, "password": "..." (optional: encrypts the file),
//    "pages": [{"size": [W, H], "items": [ITEM, ...]}]}
//
// Coordinates are points from the top left of the page. An ITEM is one of:
//
//   {"text": "...", "x": X, "y": Y, "size": S, "font": "Helvetica", "color": [r, g, b],
//    "mode": "fill" | "invisible", "angle": DEGREES}
//   {"image": {"width": PX, "height": PX, "items": [text items in pixels]}, "box": [x, y, w, h]}
//     text drawn into a picture first, so the page has no text layer for it
//   {"shape": "rect", "box": [x, y, w, h], "color": [r, g, b]}
//
// Text items keep their text layer: Quartz embeds the font with a map back
// to the characters. Prints {"pages": [{"words": [{"text": WORD, "box": [x,
// y, w, h]}]}]}: the ink bounds of every word, in points from the top left
// of the page, including the words drawn inside pictures. Invisible text has
// no ink and is reported with its place all the same.

import CoreGraphics
import CoreText
import Foundation

let spec = try JSONSerialization.jsonObject(with: FileHandle.standardInput.readDataToEndOfFile()) as! [String: Any]
let srgb = CGColorSpace(name: CGColorSpace.sRGB)!

func color(_ item: [String: Any]) -> CGColor {
    let c = item["color"] as? [Double] ?? [0, 0, 0]
    return CGColor(srgbRed: c[0], green: c[1], blue: c[2], alpha: 1)
}

/// Draws a text item on a context whose height is `h` (y down in the spec), returning word ink boxes (y down).
func drawText(_ item: [String: Any], on ctx: CGContext, height h: Double) -> [[String: Any]] {
    let text = item["text"] as! String
    let size = item["size"] as? Double ?? 12
    let font = CTFontCreateWithName((item["font"] as? String ?? "Helvetica") as CFString, size, nil)
    let attrs: [NSAttributedString.Key: Any] = [
        NSAttributedString.Key(kCTFontAttributeName as String): font,
        NSAttributedString.Key(kCTForegroundColorAttributeName as String): color(item),
    ]
    let line = CTLineCreateWithAttributedString(NSAttributedString(string: text, attributes: attrs))
    let x = item["x"] as? Double ?? 0, y = item["y"] as? Double ?? 0
    let ascent = CTFontGetAscent(font)
    let angle = (item["angle"] as? Double ?? 0) * .pi / 180
    let origin = CGPoint(x: x, y: h - y - ascent)
    let place = CGAffineTransform(translationX: origin.x, y: origin.y).rotated(by: angle)
    var words: [[String: Any]] = []
    let ns = text as NSString
    var start = 0
    for word in text.split(separator: " ", omittingEmptySubsequences: true) {
        let range = ns.range(of: String(word), range: NSRange(location: start, length: ns.length - start))
        start = range.location + range.length
        let sub = CTLineCreateWithAttributedString(NSAttributedString(string: String(word), attributes: attrs))
        ctx.textPosition = .zero
        let ink = CTLineGetImageBounds(sub, ctx)
        let x0 = CTLineGetOffsetForStringIndex(line, range.location, nil)
        let r = ink.offsetBy(dx: x0, dy: 0).applying(place)
        words.append(["text": String(word), "box": [r.minX, h - r.maxY, r.width, r.height]])
    }
    ctx.saveGState()
    if item["mode"] as? String == "invisible" { ctx.setTextDrawingMode(.invisible) }
    ctx.translateBy(x: origin.x, y: origin.y)
    ctx.rotate(by: angle)
    ctx.textPosition = .zero
    CTLineDraw(line, ctx)
    ctx.restoreGState()
    return words
}

let url = URL(fileURLWithPath: spec["output"] as! String)
var info: [CFString: Any] = [kCGPDFContextCreator: "DocSan tests"]
if let password = spec["password"] as? String {
    info[kCGPDFContextUserPassword] = password
    info[kCGPDFContextOwnerPassword] = password + "-owner"
}
var media = CGRect(x: 0, y: 0, width: 595, height: 842)
let pdf = CGContext(url as CFURL, mediaBox: &media, info as CFDictionary)!
var report: [[String: Any]] = []
for page in spec["pages"] as! [[String: Any]] {
    let size = page["size"] as? [Double] ?? [595, 842]
    var box = CGRect(x: 0, y: 0, width: size[0], height: size[1])
    let pageInfo: [CFString: Any] = [kCGPDFContextMediaBox: Data(bytes: &box, count: MemoryLayout<CGRect>.size)]
    pdf.beginPDFPage(pageInfo as CFDictionary)
    let h = size[1]
    var words: [[String: Any]] = []
    for item in page["items"] as? [[String: Any]] ?? [] {
        if let shape = item["shape"] as? String, let b = item["box"] as? [Double] {
            let rect = CGRect(x: b[0], y: h - b[1] - b[3], width: b[2], height: b[3])
            pdf.setFillColor(color(item))
            if shape == "ellipse" { pdf.fillEllipse(in: rect) } else { pdf.fill(rect) }
        } else if let image = item["image"] as? [String: Any], let b = item["box"] as? [Double] {
            let w = image["width"] as! Int, ih = image["height"] as! Int
            let ctx = CGContext(data: nil, width: w, height: ih, bitsPerComponent: 8, bytesPerRow: w * 4, space: srgb,
                                bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
            let bg = image["background"] as? [Double] ?? [1, 1, 1]
            ctx.setFillColor(CGColor(srgbRed: bg[0], green: bg[1], blue: bg[2], alpha: 1))
            ctx.fill(CGRect(x: 0, y: 0, width: w, height: ih))
            let sx = b[2] / Double(w), sy = b[3] / Double(ih)
            for t in image["items"] as? [[String: Any]] ?? [] {
                for word in drawText(t, on: ctx, height: Double(ih)) {
                    let r = word["box"] as! [Double]
                    words.append(["text": word["text"]!, "box": [b[0] + r[0] * sx, b[1] + r[1] * sy, r[2] * sx, r[3] * sy]])
                }
            }
            pdf.draw(ctx.makeImage()!, in: CGRect(x: b[0], y: h - b[1] - b[3], width: b[2], height: b[3]))
        } else if item["text"] != nil {
            words += drawText(item, on: pdf, height: h)
        }
    }
    pdf.endPDFPage()
    report.append(["words": words])
}
pdf.closePDF()
FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: ["pages": report]))
