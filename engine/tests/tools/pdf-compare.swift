// Test tool: compares two PDFs page by page, for the formatting tests.
//
//   pdf-compare A.pdf B.pdf DPI [OUT_DIR]
//
// When either file is a PNG (a drawing DocSan rendered), both are taken as
// one picture, cut to their ink, and the first is scaled to the second's size.
// Each page is drawn in grey at DPI. Ink is any pixel darker than 180; a
// page's score is the smaller of two shares: A's ink that lies within one
// pixel of B's ink, and B's ink within one pixel of A's. Identical pages
// score 1. With OUT_DIR, each page pair is also written side by side as
// page-N.png for a person to look at. Prints JSON: {"pages": [a, b], "scores": [...]}.

import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

func pages(_ path: String, dpi: Double) -> [(Int, Int, [UInt8])] {
    guard let doc = CGPDFDocument(URL(fileURLWithPath: path) as CFURL) else { return [] }
    var out: [(Int, Int, [UInt8])] = []
    for i in 1...max(doc.numberOfPages, 1) {
        guard let page = doc.page(at: i) else { continue }
        let box = page.getBoxRect(.mediaBox)
        let w = Int(box.width * dpi / 72), h = Int(box.height * dpi / 72)
        var gray = [UInt8](repeating: 255, count: w * h)
        let ok = gray.withUnsafeMutableBytes { buf -> Bool in
            guard let ctx = CGContext(data: buf.baseAddress, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w,
                                      space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue) else { return false }
            ctx.setFillColor(gray: 1, alpha: 1)
            ctx.fill(CGRect(x: 0, y: 0, width: w, height: h))
            ctx.scaleBy(x: dpi / 72, y: dpi / 72)
            ctx.translateBy(x: -box.minX, y: -box.minY)
            ctx.drawPDFPage(page)
            return true
        }
        if ok { out.append((w, h, gray)) }
    }
    return out
}

func ink(_ p: (Int, Int, [UInt8])) -> [Bool] { p.2.map { $0 < 180 } }

func near(_ mask: [Bool], _ w: Int, _ h: Int) -> [Bool] {
    var out = mask
    for y in 0..<h {
        for x in 0..<w where mask[y * w + x] {
            for dy in -1...1 {
                for dx in -1...1 {
                    let nx = x + dx, ny = y + dy
                    if nx >= 0, ny >= 0, nx < w, ny < h { out[ny * w + nx] = true }
                }
            }
        }
    }
    return out
}

func score(_ a: (Int, Int, [UInt8]), _ b: (Int, Int, [UInt8])) -> Double {
    guard a.0 == b.0, a.1 == b.1 else { return 0 }
    let ia = ink(a), ib = ink(b)
    let na = near(ia, a.0, a.1), nb = near(ib, b.0, b.1)
    var inA = 0, coveredA = 0, inB = 0, coveredB = 0
    for i in 0..<ia.count {
        if ia[i] { inA += 1; if nb[i] { coveredA += 1 } }
        if ib[i] { inB += 1; if na[i] { coveredB += 1 } }
    }
    if inA == 0 && inB == 0 { return 1 }
    return min(inA == 0 ? 0 : Double(coveredA) / Double(inA), inB == 0 ? 0 : Double(coveredB) / Double(inB))
}

func writePair(_ a: (Int, Int, [UInt8]), _ b: (Int, Int, [UInt8]), to path: String) {
    let w = a.0 + b.0 + 8, h = max(a.1, b.1)
    var px = [UInt8](repeating: 128, count: w * h)
    for y in 0..<a.1 { for x in 0..<a.0 { px[y * w + x] = a.2[y * a.0 + x] } }
    for y in 0..<b.1 { for x in 0..<b.0 { px[y * w + a.0 + 8 + x] = b.2[y * b.0 + x] } }
    guard let provider = CGDataProvider(data: Data(px) as CFData),
          let image = CGImage(width: w, height: h, bitsPerComponent: 8, bitsPerPixel: 8, bytesPerRow: w,
                              space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGBitmapInfo(rawValue: 0),
                              provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent),
          let dest = CGImageDestinationCreateWithURL(URL(fileURLWithPath: path) as CFURL, UTType.png.identifier as CFString, 1, nil)
    else { return }
    CGImageDestinationAddImage(dest, image, nil)
    CGImageDestinationFinalize(dest)
}

/// A grey picture cut to its ink (pixels darker than 245), for comparing drawings.
func cutToInk(_ p: (Int, Int, [UInt8])) -> (Int, Int, [UInt8]) {
    let (w, h, g) = p
    var x0 = w, y0 = h, x1 = -1, y1 = -1
    for y in 0..<h { for x in 0..<w where g[y * w + x] < 245 { x0 = min(x0, x); x1 = max(x1, x); y0 = min(y0, y); y1 = max(y1, y) } }
    guard x1 >= x0 else { return p }
    let cw = x1 - x0 + 1, ch = y1 - y0 + 1
    var out = [UInt8](repeating: 255, count: cw * ch)
    for y in 0..<ch { for x in 0..<cw { out[y * cw + x] = g[(y + y0) * w + x + x0] } }
    return (cw, ch, out)
}

func resized(_ p: (Int, Int, [UInt8]), width: Int, height: Int) -> (Int, Int, [UInt8]) {
    var out = [UInt8](repeating: 255, count: width * height)
    let source = p.2
    guard let provider = CGDataProvider(data: Data(source) as CFData),
          let image = CGImage(width: p.0, height: p.1, bitsPerComponent: 8, bitsPerPixel: 8, bytesPerRow: p.0,
                              space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGBitmapInfo(rawValue: 0),
                              provider: provider, decode: nil, shouldInterpolate: true, intent: .defaultIntent) else { return p }
    out.withUnsafeMutableBytes { buf in
        guard let ctx = CGContext(data: buf.baseAddress, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width,
                                  space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue) else { return }
        ctx.interpolationQuality = .high
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
    }
    return (width, height, out)
}

func picture(_ path: String) -> [(Int, Int, [UInt8])] {
    guard let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil),
          let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { return [] }
    let w = image.width, h = image.height
    var gray = [UInt8](repeating: 255, count: w * h)
    gray.withUnsafeMutableBytes { buf in
        guard let ctx = CGContext(data: buf.baseAddress, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w,
                                  space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue) else { return }
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
    }
    return [(w, h, gray)]
}

let args = CommandLine.arguments
let dpi = Double(args[3]) ?? 60
var a = args[1].hasSuffix(".png") ? picture(args[1]) : pages(args[1], dpi: dpi)
var b = args[2].hasSuffix(".png") ? picture(args[2]) : pages(args[2], dpi: dpi)
if args[1].hasSuffix(".png") || args[2].hasSuffix(".png"), let first = a.first, let second = b.first {
    // A drawing: both cut to their ink, the first scaled to the second's size.
    let target = cutToInk(second)
    a = [resized(cutToInk(first), width: target.0, height: target.1)]
    b = [target]
}
var scores: [Double] = []
for i in 0..<min(a.count, b.count) {
    scores.append(score(a[i], b[i]))
    if args.count > 4 { writePair(a[i], b[i], to: "\(args[4])/page-\(i + 1).png") }
}
let result: [String: Any] = ["pages": [a.count, b.count], "scores": scores]
print(String(data: try! JSONSerialization.data(withJSONObject: result), encoding: .utf8)!)
