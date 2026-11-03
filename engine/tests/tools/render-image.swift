// Test-only renderer for synthetic image fixtures. Reads a JSON spec on stdin:
//
//   {"output": PATH, "format": "png" | "jpeg", "width": W, "height": H,
//    "background": [r, g, b, a], "orientation": 1 | 3 | 6 | 8,
//    "items": [{"text": "...", "x": X, "y": Y, "size": S, "font": "Helvetica",
//               "color": [r, g, b], "angle": DEGREES}]}
//
// An item with "shape": "ellipse" or "rect" draws that shape in "box": [x, y,
// w, h] instead of text, filled, or outlined when "stroke" gives a width.
//
// Colours are 0...1. x and y place the top left of the text in the upright
// image. With an orientation other than 1 the pixels are stored rotated and
// tagged, so a viewer that applies the tag shows the upright image.
//
// Prints {"words": [{"text": WORD, "box": [x, y, w, h]}]}: the ink bounds of
// every word in pixels of the upright image, origin top left. Tests use them
// to check that a mask covers every pixel of a word.

import CoreGraphics
import CoreText
import Foundation
import ImageIO
import UniformTypeIdentifiers

let spec = try JSONSerialization.jsonObject(with: FileHandle.standardInput.readDataToEndOfFile()) as! [String: Any]
let w = spec["width"] as! Int, h = spec["height"] as! Int
let format = spec["format"] as? String ?? "png"
let bg = spec["background"] as? [Double] ?? [1, 1, 1, 1]
let orientation = spec["orientation"] as? Int ?? 1
let srgb = CGColorSpace(name: CGColorSpace.sRGB)!
let alpha = bg.count == 4 && bg[3] < 1

func context(_ width: Int, _ height: Int) -> CGContext {
    let info = alpha ? CGImageAlphaInfo.premultipliedLast.rawValue : CGImageAlphaInfo.noneSkipLast.rawValue
    return CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width * 4,
                     space: srgb, bitmapInfo: info)!
}

var words: [[String: Any]] = []
let upright = context(w, h)
upright.setFillColor(CGColor(srgbRed: bg[0], green: bg[1], blue: bg[2], alpha: bg.count == 4 ? bg[3] : 1))
upright.fill(CGRect(x: 0, y: 0, width: w, height: h))
for item in spec["items"] as? [[String: Any]] ?? [] {
    if let shape = item["shape"] as? String, let b = item["box"] as? [Double] {
        let c = item["color"] as? [Double] ?? [0, 0, 0]
        let rect = CGRect(x: b[0], y: Double(h) - b[1] - b[3], width: b[2], height: b[3])
        let path = shape == "ellipse" ? CGPath(ellipseIn: rect, transform: nil) : CGPath(rect: rect, transform: nil)
        upright.addPath(path)
        if let stroke = item["stroke"] as? Double {
            upright.setStrokeColor(CGColor(srgbRed: c[0], green: c[1], blue: c[2], alpha: 1))
            upright.setLineWidth(stroke)
            upright.strokePath()
        } else {
            upright.setFillColor(CGColor(srgbRed: c[0], green: c[1], blue: c[2], alpha: 1))
            upright.fillPath()
        }
        continue
    }
    let text = item["text"] as! String
    let size = item["size"] as? Double ?? 24
    let font = CTFontCreateWithName((item["font"] as? String ?? "Helvetica") as CFString, size, nil)
    let c = item["color"] as? [Double] ?? [0, 0, 0]
    let attrs: [NSAttributedString.Key: Any] = [
        NSAttributedString.Key(kCTFontAttributeName as String): font,
        NSAttributedString.Key(kCTForegroundColorAttributeName as String): CGColor(srgbRed: c[0], green: c[1], blue: c[2], alpha: 1),
    ]
    let line = CTLineCreateWithAttributedString(NSAttributedString(string: text, attributes: attrs))
    let x = item["x"] as? Double ?? 0, y = item["y"] as? Double ?? 0
    let ascent = CTFontGetAscent(font)
    let angle = (item["angle"] as? Double ?? 0) * .pi / 180
    let origin = CGPoint(x: x, y: Double(h) - y - ascent)
    let place = CGAffineTransform(translationX: origin.x, y: origin.y).rotated(by: angle)
    let ns = text as NSString
    var start = 0
    for word in text.split(separator: " ", omittingEmptySubsequences: true) {
        let range = ns.range(of: String(word), range: NSRange(location: start, length: ns.length - start))
        start = range.location + range.length
        let sub = CTLineCreateWithAttributedString(NSAttributedString(string: String(word), attributes: attrs))
        upright.textPosition = .zero  // image bounds are relative to the text position
        let ink = CTLineGetImageBounds(sub, upright)
        let x0 = CTLineGetOffsetForStringIndex(line, range.location, nil)
        let r = ink.offsetBy(dx: x0, dy: 0).applying(place)
        words.append(["text": String(word), "box": [r.minX, Double(h) - r.maxY, r.width, r.height]])
    }
    upright.saveGState()
    upright.translateBy(x: origin.x, y: origin.y)
    upright.rotate(by: angle)
    upright.textPosition = .zero
    CTLineDraw(line, upright)
    upright.restoreGState()
}

var image = upright.makeImage()!
if orientation != 1 {
    let rotated = orientation == 3 ? context(w, h) : context(h, w)
    switch orientation {
    case 3: rotated.translateBy(x: Double(w), y: Double(h)); rotated.rotate(by: .pi)
    case 6: rotated.translateBy(x: Double(h), y: 0); rotated.rotate(by: .pi / 2)
    case 8: rotated.translateBy(x: 0, y: Double(w)); rotated.rotate(by: -.pi / 2)
    default: fatalError("unsupported orientation")
    }
    rotated.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
    image = rotated.makeImage()!
}

let url = URL(fileURLWithPath: spec["output"] as! String)
let type: UTType = format == "png" ? .png : .jpeg
let dest = CGImageDestinationCreateWithURL(url as CFURL, type.identifier as CFString, 1, nil)!
var props: [CFString: Any] = [kCGImagePropertyOrientation: orientation]
if format == "jpeg" { props[kCGImageDestinationLossyCompressionQuality] = spec["quality"] as? Double ?? 0.9 }
CGImageDestinationAddImage(dest, image, props as CFDictionary)
guard CGImageDestinationFinalize(dest) else { fatalError("cannot write image") }
FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: ["words": words]))
