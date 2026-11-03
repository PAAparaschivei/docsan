// docsan-vision: Apple Vision text recognition and pixel masking for DocSan.
//
// The engine runs this helper as a child process inside the app's sandbox. It
// reads one JSON request from stdin and writes one JSON response to stdout:
//
//   {"command": "probe", "input": PATH}
//   {"command": "ocr", "input": PATH, "rotations": [90, 270], "tile": 1536, "overlap": 384, "zoom": 2}
//   {"command": "fit", "input": PATH, "margin": 2, "boxes": [[x, y, w, h, reach, vertical], ...]}
//   {"command": "print", "input": PATH}
//   {"command": "render-pdf", "input": PDF, "output": PNG, "dpi": 220}
//   {"command": "render-emf", "input": EMF, "output": PNG, "dpi": 220}
//   {"command": "pdf-read", "input": PDF, "output": DIR, "dpi": 200, "max_pixels": 36000000,
//    "ocr_dpi": 400, "max_ocr_pixels": 100000000}
//   {"command": "pdf-text", "input": PDF}
//   {"command": "mask", "input": PATH, "output": PATH, "format": "raw" | "jpeg" | "png",
//    "quality": 0.92, "boxes": [[x, y, w, h], ...]}
//
// Boxes are pixels of the upright image with the origin at the top left. The
// helper never decides what to mask: it finds text and paints the boxes the
// engine gives it. Every image is redrawn into a fresh sRGB canvas, which
// applies the EXIF orientation, reads only the first frame and, for images
// with transparency, clears the colour of fully transparent pixels. "raw"
// writes the canvas as 8-bit RGBA (or RGB without transparency) rows, which
// the engine encodes as PNG itself. "png" writes an 8-bit PNG, grey when every
// pixel is neutral, for a flattened PDF page (the engine takes its compressed
// rows as they are). "render-pdf" draws the first page of a
// PDF (a vector diagram LibreOffice converted) on white at the given
// resolution and crops it to the drawing, for reading and masking like any
// picture. "render-emf" draws an EMF drawing itself (EMF.swift) and reports
// its text with the box of every character. "ocr" with "tile" also reads an
// image larger than the tile in overlapping tiles (see tileRects); each tile's
// passes carry its rectangle. "zoom" reads the image enlarged that many times,
// smoothly resampled, and reports every box in the image's own pixels. "pdf-read" draws every page of a
// PDF document and reads its text layer and structure (PDF.swift); "pdf-text"
// returns each page's text, for the verifier. "fit" turns Vision's loose word boxes
// into masks that follow the ink (see fit below). "print" returns a
// fingerprint of what the picture draws, to recognise a logo again (see
// fingerprint below). Every request may set
// "crop": [left, top, right, bottom] as fractions of the upright image, for a
// picture that a document shows cropped: everything then works on the visible
// part only, and the hidden part never reaches the output. Every request may
// also set "orientation":
// false, which skips the EXIF orientation; only the engine's tests do.

import CoreGraphics
import CoreML
import Foundation
import ImageIO
import UniformTypeIdentifiers
import Vision

struct Failure: Error {
    let message: String
}

let maxPixels = 120_000_000

struct Canvas {
    let context: CGContext
    let width: Int
    let height: Int
    let frames: Int
    let orientation: Int
    let hasAlpha: Bool
    let type: String

    var image: CGImage {
        get throws {
            guard let image = context.makeImage() else { throw Failure(message: "cannot read the canvas") }
            return image
        }
    }
}

func open(_ path: String, orient: Bool, crop: [Double] = []) throws -> Canvas {
    let url = URL(fileURLWithPath: path)
    guard let source = CGImageSourceCreateWithURL(url as CFURL, [kCGImageSourceShouldCache: false] as CFDictionary),
          let type = CGImageSourceGetType(source) as String?
    else { throw Failure(message: "cannot open the image") }
    let frames = CGImageSourceGetCount(source)
    guard frames >= 1,
          let props = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
          let width = props[kCGImagePropertyPixelWidth] as? Int,
          let height = props[kCGImagePropertyPixelHeight] as? Int,
          width > 0, height > 0
    else { throw Failure(message: "the image has no size") }
    guard width * height <= maxPixels else { throw Failure(message: "the image is too large") }
    // ImageIO decodes a truncated file and fills the rest; refuse it instead.
    guard CGImageSourceGetStatus(source) == .statusComplete,
          CGImageSourceGetStatusAtIndex(source, 0) == .statusComplete
    else { throw Failure(message: "the image file is incomplete") }
    let orientation = orient ? props[kCGImagePropertyOrientation] as? Int ?? 1 : 1
    // A thumbnail at full size, made from the image itself rather than an
    // embedded preview, with the orientation applied.
    let options: [CFString: Any] = [
        kCGImageSourceCreateThumbnailFromImageAlways: true,
        kCGImageSourceCreateThumbnailWithTransform: orient,
        kCGImageSourceThumbnailMaxPixelSize: max(width, height),
        kCGImageSourceShouldCacheImmediately: true,
    ]
    guard let upright = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary) else {
        throw Failure(message: "cannot decode the image")
    }
    let rotated = (5...8).contains(orientation)
    let (fullW, fullH) = rotated ? (height, width) : (width, height)
    guard upright.width == fullW, upright.height == fullH else {
        throw Failure(message: "decoded size \(upright.width)x\(upright.height) differs from \(fullW)x\(fullH)")
    }
    var visible = upright
    if crop.count == 4, crop.contains(where: { $0 > 0 }) {
        let x0 = Int((crop[0] * Double(fullW)).rounded()), y0 = Int((crop[1] * Double(fullH)).rounded())
        let x1 = fullW - Int((crop[2] * Double(fullW)).rounded()), y1 = fullH - Int((crop[3] * Double(fullH)).rounded())
        guard x1 - x0 >= 1, y1 - y0 >= 1, let part = upright.cropping(to: CGRect(x: x0, y: y0, width: x1 - x0, height: y1 - y0))
        else { throw Failure(message: "the crop leaves nothing of the image") }
        visible = part
    }
    let (w, h) = (visible.width, visible.height)
    let hasAlpha = ![.none, .noneSkipFirst, .noneSkipLast].contains(upright.alphaInfo)
    // Premultiplied alpha stores fully transparent pixels as zero, so colour
    // hidden under transparency does not survive the redraw.
    let info = hasAlpha ? CGImageAlphaInfo.premultipliedLast.rawValue : CGImageAlphaInfo.noneSkipLast.rawValue
    guard let srgb = CGColorSpace(name: CGColorSpace.sRGB),
          let context = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
                                  space: srgb, bitmapInfo: info)
    else { throw Failure(message: "cannot create a canvas") }
    context.interpolationQuality = .none
    context.draw(visible, in: CGRect(x: 0, y: 0, width: w, height: h))
    return Canvas(context: context, width: w, height: h, frames: frames, orientation: orientation,
                  hasAlpha: hasAlpha, type: type)
}

/// The canvas turned a quarter clockwise (90) or anticlockwise (270), so that
/// vertical labels read horizontally.
func turned(_ canvas: Canvas, by degrees: Int) throws -> Canvas {
    let w = canvas.width, h = canvas.height
    guard let srgb = CGColorSpace(name: CGColorSpace.sRGB),
          let ctx = CGContext(data: nil, width: h, height: w, bitsPerComponent: 8, bytesPerRow: h * 4,
                              space: srgb, bitmapInfo: canvas.context.bitmapInfo.rawValue)
    else { throw Failure(message: "cannot create a canvas") }
    if degrees == 90 {
        ctx.translateBy(x: 0, y: Double(w))
        ctx.rotate(by: -.pi / 2)
    } else {
        ctx.translateBy(x: Double(h), y: 0)
        ctx.rotate(by: .pi / 2)
    }
    ctx.draw(try canvas.image, in: CGRect(x: 0, y: 0, width: w, height: h))
    return Canvas(context: ctx, width: h, height: w, frames: canvas.frames, orientation: canvas.orientation,
                  hasAlpha: canvas.hasAlpha, type: canvas.type)
}

/// Overlapping squares that cover an image too large to read whole. Vision
/// shrinks a large image before reading it, and text a few pixels high is
/// lost; read tile by tile, it stays at its own size. Neighbouring tiles
/// overlap by `overlap` pixels, so a word up to that long is whole in at
/// least one tile.
func tileRects(width: Int, height: Int, size: Int, overlap: Int) -> [CGRect] {
    guard max(width, height) > size, size > overlap, overlap >= 0 else { return [] }
    func starts(_ length: Int) -> [Int] {
        guard length > size else { return [0] }
        return Array(stride(from: 0, to: length - size, by: size - overlap)) + [length - size]
    }
    return starts(height).flatMap { y in
        starts(width).map { x in CGRect(x: x, y: y, width: min(size, width), height: min(size, height)) }
    }
}

/// One tile of a canvas, as a canvas of its own.
func piece(_ canvas: Canvas, _ r: CGRect) throws -> Canvas {
    guard let part = try canvas.image.cropping(to: r), let srgb = CGColorSpace(name: CGColorSpace.sRGB),
          let ctx = CGContext(data: nil, width: Int(r.width), height: Int(r.height), bitsPerComponent: 8,
                              bytesPerRow: Int(r.width) * 4, space: srgb, bitmapInfo: canvas.context.bitmapInfo.rawValue)
    else { throw Failure(message: "cannot cut a tile") }
    ctx.draw(part, in: CGRect(x: 0, y: 0, width: r.width, height: r.height))
    return Canvas(context: ctx, width: Int(r.width), height: Int(r.height), frames: canvas.frames,
                  orientation: canvas.orientation, hasAlpha: canvas.hasAlpha, type: canvas.type)
}

/// A box found on a turned canvas, back in the upright image.
func unturn(_ b: [Double], by degrees: Int, upright: Canvas) -> [Double] {
    let (u, v, bw, bh) = (b[0], b[1], b[2], b[3])
    let box = degrees == 90
        ? [v, Double(upright.height) - u - bw, bh, bw]
        : [Double(upright.width) - v - bh, u, bh, bw]
    return box.map { ($0 * 10).rounded() / 10 }
}

func describe(_ canvas: Canvas) -> [String: Any] {
    ["width": canvas.width, "height": canvas.height, "frames": canvas.frames,
     "orientation": canvas.orientation, "alpha": canvas.hasAlpha, "type": canvas.type]
}

/// A Vision rectangle (normalized, origin bottom left) in pixels from the top left.
func pixels(_ r: CGRect, _ canvas: Canvas) -> [Double] {
    let w = Double(canvas.width), h = Double(canvas.height)
    let box = [r.minX * w, (1 - r.maxY) * h, r.width * w, r.height * h]
    return box.map { ($0 * 10).rounded() / 10 }
}

/// Seconds a text recognition request may take before it is cancelled. One
/// normally takes well under two; now and then Vision waits forever for the
/// Neural Engine, and the request is tried again on the CPU.
let recognitionTimeout = 60.0

/// Runs one text request, cancelling it when Vision does not answer in time.
func perform(_ request: VNRecognizeTextRequest, on image: CGImage) throws -> Bool {
    let done = DispatchSemaphore(value: 0)
    nonisolated(unsafe) var failure: Error?
    let handler = VNImageRequestHandler(cgImage: image, options: [:])
    DispatchQueue.global(qos: .userInitiated).async {
        do { try handler.perform([request]) } catch { failure = error }
        done.signal()
    }
    if done.wait(timeout: .now() + recognitionTimeout) == .timedOut {
        request.cancel()
        return false
    }
    if let failure { throw failure }
    return true
}

func recognize(_ canvas: Canvas, correction: Bool, map: ([Double]) -> [Double] = { $0 }) throws -> [[String: Any]] {
    func textRequest(cpu: Bool) -> VNRecognizeTextRequest {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = correction
        request.recognitionLanguages = ["en-US", "fr-FR"]
        // The default ignores text smaller than 1/32 of the image height, which
        // drops most of the text in a large screenshot.
        request.minimumTextHeight = Float(min(1.0, 6.0 / Double(canvas.height)))
        if cpu, let device = MLComputeDevice.allComputeDevices.first(where: { if case .cpu = $0 { true } else { false } }) {
            request.setComputeDevice(device, for: .main)
        }
        return request
    }
    let image = try canvas.image
    var request = textRequest(cpu: false)
    if try !perform(request, on: image) {
        request = textRequest(cpu: true)
        guard try perform(request, on: image) else { throw Failure(message: "text recognition did not answer") }
    }
    var lines: [[String: Any]] = []
    for observation in request.results ?? [] {
        var candidates: [[String: Any]] = []
        for candidate in observation.topCandidates(3) {
            let text = candidate.string
            var chars: [Any] = []
            let scalars = text.unicodeScalars
            var i = scalars.startIndex
            while i < scalars.endIndex {
                let next = scalars.index(after: i)
                if let box = try? candidate.boundingBox(for: i..<next)?.boundingBox, box.width > 0, box.height > 0 {
                    chars.append(map(pixels(box, canvas)))
                } else {
                    chars.append(NSNull())
                }
                i = next
            }
            candidates.append(["text": text, "confidence": Double(candidate.confidence), "chars": chars])
        }
        lines.append(["box": map(pixels(observation.boundingBox, canvas)), "candidates": candidates])
    }
    return lines
}

/// Fits a mask to the ink of the word Vision boxed. Vision's boxes are loose
/// on one side and can miss a swash on the other, so the helper looks along
/// the line, up to `reach` pixels past the box, for runs of ink columns.
/// It covers every run that touches the middle of the box or lies mostly
/// inside it: the mask hugs the word, follows ink that leaves the box and
/// leaves neighbouring words alone. Ink is any pixel whose brightness differs
/// from the commonest brightness around the word.
///
/// Box: [x, y, w, h, reach, vertical (0 or 1)]. Returns the fitted box plus a
/// `margin` on every side, or the box as given when no ink is found.
func fit(_ canvas: Canvas, box b: [Double], margin: Int, guards: [[Double]] = []) -> [Double] {
    guard b.count == 6, let base = canvas.context.data else { return Array(b.prefix(4)) }
    let px = base.assumingMemoryBound(to: UInt8.self)
    let stride = canvas.context.bytesPerRow, w = canvas.width, h = canvas.height
    let vertical = b[5] != 0
    // Positions along the line (a) and across it (c).
    let (alongMax, acrossMax) = vertical ? (h, w) : (w, h)
    let boxA0 = Int(floor(vertical ? b[1] : b[0])), boxA1 = Int(ceil(vertical ? b[1] + b[3] : b[0] + b[2])) - 1
    var c0 = max(0, Int(floor(vertical ? b[0] : b[1]))), c1 = min(acrossMax - 1, Int(ceil(vertical ? b[0] + b[2] : b[1] + b[3])) - 1)
    let size = Double(c1 - c0 + 1)
    let reach = Int(b[4])
    let a0 = max(0, boxA0 - reach), a1 = min(alongMax - 1, boxA1 + reach)
    guard a1 > a0, c1 >= c0 else { return Array(b.prefix(4)) }
    func luma(_ a: Int, _ c: Int) -> Int {
        let (x, y) = vertical ? (c, a) : (a, c)
        let i = y * stride + x * 4
        return (Int(px[i]) * 3 + Int(px[i + 1]) * 6 + Int(px[i + 2])) / 10
    }
    var histogram = [Int](repeating: 0, count: 32)
    for a in a0...a1 { for c in c0...c1 { histogram[luma(a, c) / 8] += 1 } }
    let background = (histogram.indices.max { histogram[$0] < histogram[$1] } ?? 31) * 8 + 4
    func ink(_ a: Int, _ c: Int) -> Bool { abs(luma(a, c) - background) > 40 }
    let columns = (a0...a1).map { a in (c0...c1).contains { ink(a, $0) } }
    // Runs of ink columns; gaps narrower than a letter gap join them.
    let gap = max(2, Int(0.12 * size))
    var runs: [(Int, Int)] = []
    var a = a0
    while a <= a1 {
        guard columns[a - a0] else { a += 1; continue }
        var end = a
        while end + 1 <= a1, columns[end + 1 - a0] { end += 1 }
        if let last = runs.last, a - last.1 - 1 < gap { runs[runs.count - 1].1 = end } else { runs.append((a, end)) }
        a = end + 1
    }
    let length = Double(boxA1 - boxA0 + 1)
    let core = (Double(boxA0) + 0.2 * length, Double(boxA1) - 0.2 * length)
    let chosen = runs.filter { run in
        let touchesCore = Double(run.1) >= core.0 && Double(run.0) <= core.1
        let inside = max(0, min(run.1, boxA1) - max(run.0, boxA0) + 1)
        return touchesCore || Double(inside) > 0.5 * Double(run.1 - run.0 + 1)
    }
    guard let first = chosen.first, let last = chosen.last else { return Array(b.prefix(4)) }
    var n0 = first.0, n1 = last.1
    // A loose OCR box can include the leading strokes of the next word.
    // Use nearby unmatched words as guards and stop at the intervening ink gap.
    for g in guards where g.count == 4 {
        let ga0=Int(vertical ? g[1] : g[0]), ga1=Int(vertical ? g[1]+g[3] : g[0]+g[2])
        let gc0=Int(vertical ? g[0] : g[1]), gc1=Int(vertical ? g[0]+g[2] : g[1]+g[3])
        guard min(c1,gc1)>max(c0,gc0) else { continue }
        let right=ga0>(boxA0+boxA1)/2
        guard right ? ga0-boxA1<Int(1.5*size) : boxA0-ga1<Int(1.5*size) else { continue }
        let low=max(a0,right ? min(boxA1,ga0)-Int(0.8*size) : ga1-Int(0.3*size))
        let high=min(a1,right ? ga0+Int(0.3*size) : max(boxA0,ga1)+Int(0.8*size))
        guard low<high else { continue }
        var gaps=[(Int,Int)](), p=low
        while p<=high {
            if columns[p-a0] { p += 1;continue }
            let start=p
            while p<=high && !columns[p-a0] { p += 1 }
            if start>low && p<=high { gaps.append((start,p-1)) }
        }
        if let gap=gaps.max(by:{$0.1-$0.0<$1.1-$1.0}), gap.1-gap.0+1>=max(3,Int(0.12*size)) {
            if right { n1=min(n1,gap.0-1) } else { n0=max(n0,gap.1+1) }
        }
    }
    if n1<n0 { n0=first.0; n1=last.1 } // conservative fallback on contradictory guards
    // Across the line: follow ink above and below the box, up to a third of the text height.
    func inkRow(_ c: Int) -> Bool { (n0...n1).contains { ink($0, c) } }
    let occupied = (c0...c1).filter { inkRow($0) }
    if let first = occupied.first, let last = occupied.last { c0 = first; c1 = last }
    let limit = Int(0.3 * size)
    var grown = 0
    while grown < limit, c0 > 0, inkRow(c0) { c0 -= 1; grown += 1 }
    grown = 0
    while grown < limit, c1 < acrossMax - 1, inkRow(c1) { c1 += 1; grown += 1 }
    let fa0 = max(0, n0 - margin), fa1 = min(alongMax - 1, n1 + margin)
    let fc0 = max(0, c0 - margin), fc1 = min(acrossMax - 1, c1 + margin)
    return vertical
        ? [Double(fc0), Double(fa0), Double(fc1 - fc0 + 1), Double(fa1 - fa0 + 1)]
        : [Double(fa0), Double(fc0), Double(fa1 - fa0 + 1), Double(fc1 - fc0 + 1)]
}

func mask(_ canvas: Canvas, boxes: [[Double]]) {
    let ctx = canvas.context
    ctx.setBlendMode(.copy)
    ctx.setShouldAntialias(false)
    ctx.setFillColor(CGColor(srgbRed: 0, green: 0, blue: 0, alpha: 1))
    let w = Double(canvas.width), h = Double(canvas.height)
    for b in boxes where b.count == 4 {
        // Round outward to whole pixels so no partly covered pixel remains.
        let x0 = max(0, floor(b[0])), y0 = max(0, floor(b[1]))
        let x1 = min(w, ceil(b[0] + b[2])), y1 = min(h, ceil(b[1] + b[3]))
        guard x1 > x0, y1 > y0 else { continue }
        ctx.fill(CGRect(x: x0, y: h - y1, width: x1 - x0, height: y1 - y0))
    }
}

/// A fingerprint of what a picture draws, to find the same logo again at
/// another size, margin or compression. The picture is flattened on white
/// in grey, cut to its ink (pixels that differ from the commonest border
/// brightness), squeezed to 32 by 32 pixels, and each of the 63 lowest
/// cosine frequencies but the average becomes one bit: above their median
/// or not (a perceptual hash). The ink's width over its height is returned
/// beside it; a picture with no ink has no fingerprint.
func fingerprint(_ canvas: Canvas) throws -> [String: Any] {
    let gray = CGColorSpaceCreateDeviceGray()
    func context(_ w: Int, _ h: Int) throws -> CGContext {
        guard let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w, space: gray,
                                  bitmapInfo: CGImageAlphaInfo.none.rawValue)
        else { throw Failure(message: "cannot create a canvas") }
        ctx.interpolationQuality = .high
        ctx.setFillColor(gray: 1, alpha: 1)
        ctx.fill(CGRect(x: 0, y: 0, width: w, height: h))
        return ctx
    }
    let scale = min(1, 512 / Double(max(canvas.width, canvas.height)))
    let w = max(1, Int((Double(canvas.width) * scale).rounded())), h = max(1, Int((Double(canvas.height) * scale).rounded()))
    let flat = try context(w, h)
    flat.draw(try canvas.image, in: CGRect(x: 0, y: 0, width: w, height: h))
    guard let base = flat.data, let flatImage = flat.makeImage() else { throw Failure(message: "cannot read the canvas") }
    let px = base.assumingMemoryBound(to: UInt8.self)
    let stride = flat.bytesPerRow
    var histogram = [Int](repeating: 0, count: 32)
    for x in 0..<w { histogram[Int(px[x]) / 8] += 1; histogram[Int(px[(h - 1) * stride + x]) / 8] += 1 }
    for y in 0..<h { histogram[Int(px[y * stride]) / 8] += 1; histogram[Int(px[y * stride + w - 1]) / 8] += 1 }
    let background = (histogram.indices.max { histogram[$0] < histogram[$1] } ?? 31) * 8 + 4
    var x0 = w, y0 = h, x1 = -1, y1 = -1, inked = 0
    for y in 0..<h {
        for x in 0..<w where abs(Int(px[y * stride + x]) - background) > 32 {
            x0 = min(x0, x); x1 = max(x1, x); y0 = min(y0, y); y1 = max(y1, y)
            inked += 1
        }
    }
    let describe: [String: Any] = ["width": canvas.width, "height": canvas.height]
    guard x1 >= x0, y1 >= y0, inked * 1000 >= w * h else { return describe.merging(["print": NSNull()]) { a, _ in a } }
    // Memory rows run from the top; CGImage cropping does too.
    guard let ink = flatImage.cropping(to: CGRect(x: x0, y: y0, width: x1 - x0 + 1, height: y1 - y0 + 1)) else {
        throw Failure(message: "cannot read the canvas")
    }
    let n = 32
    let small = try context(n, n)
    small.draw(ink, in: CGRect(x: 0, y: 0, width: n, height: n))
    guard let sbase = small.data else { throw Failure(message: "cannot read the canvas") }
    let s = sbase.assumingMemoryBound(to: UInt8.self)
    var value = [[Double]](repeating: [Double](repeating: 0, count: n), count: n)
    for y in 0..<n { for x in 0..<n { value[y][x] = Double(s[y * small.bytesPerRow + x]) } }
    var cosines = [[Double]](repeating: [Double](repeating: 0, count: n), count: 8)
    for u in 0..<8 { for x in 0..<n { cosines[u][x] = cos(Double(2 * x + 1) * Double(u) * .pi / Double(2 * n)) } }
    var coefficients: [Double] = []
    for v in 0..<8 {
        for u in 0..<8 where u + v > 0 {
            var sum = 0.0
            for y in 0..<n { for x in 0..<n { sum += value[y][x] * cosines[u][x] * cosines[v][y] } }
            coefficients.append(sum)
        }
    }
    let median = coefficients.sorted()[coefficients.count / 2]
    var bits: UInt64 = 0
    for (i, c) in coefficients.enumerated() where c > median { bits |= 1 << UInt64(i) }
    let aspect = (Double(x1 - x0 + 1) / Double(y1 - y0 + 1) * 1000).rounded() / 1000
    return describe.merging(["print": String(format: "%016llx", bits), "aspect": aspect]) { a, _ in a }
}

func writeRaw(_ canvas: Canvas, to path: String) throws {
    guard let base = canvas.context.data else { throw Failure(message: "cannot read the canvas") }
    let stride = canvas.context.bytesPerRow
    let rgba = UnsafeBufferPointer(start: base.assumingMemoryBound(to: UInt8.self), count: stride * canvas.height)
    var out = Data(capacity: canvas.width * canvas.height * (canvas.hasAlpha ? 4 : 3))
    for y in 0..<canvas.height {
        let row = rgba[(y * stride)..<(y * stride + canvas.width * 4)]
        if canvas.hasAlpha {
            out.append(contentsOf: row)
        } else {
            var rgb = [UInt8](); rgb.reserveCapacity(canvas.width * 3)
            var i = row.startIndex
            while i < row.endIndex { rgb.append(row[i]); rgb.append(row[i + 1]); rgb.append(row[i + 2]); i += 4 }
            out.append(contentsOf: rgb)
        }
    }
    guard FileManager.default.createFile(atPath: path, contents: out) else {
        throw Failure(message: "cannot write raw output")
    }
}

/// The canvas as an 8-bit PNG without transparency: grey when every pixel is
/// neutral (a text page is a third of its size that way), RGB otherwise. The
/// grey values are the canvas's own, copied, not converted.
func writePNG(_ canvas: Canvas, to path: String) throws -> Bool {
    guard !canvas.hasAlpha, let base = canvas.context.data else { throw Failure(message: "a page has no transparency") }
    let px = base.assumingMemoryBound(to: UInt8.self)
    let stride = canvas.context.bytesPerRow, w = canvas.width, h = canvas.height
    var neutral = true
    scan: for y in 0..<h {
        for x in 0..<w {
            let i = y * stride + x * 4
            if px[i] != px[i + 1] || px[i + 1] != px[i + 2] { neutral = false; break scan }
        }
    }
    var image = try canvas.image
    if neutral {
        var grey = [UInt8](repeating: 0, count: w * h)
        for y in 0..<h { for x in 0..<w { grey[y * w + x] = px[y * stride + x * 4] } }
        guard let provider = CGDataProvider(data: Data(grey) as CFData),
              let made = CGImage(width: w, height: h, bitsPerComponent: 8, bitsPerPixel: 8, bytesPerRow: w,
                                 space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGBitmapInfo(rawValue: 0),
                                 provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent)
        else { throw Failure(message: "cannot write a grey page") }
        image = made
    }
    guard let dest = CGImageDestinationCreateWithURL(URL(fileURLWithPath: path) as CFURL, UTType.png.identifier as CFString, 1, nil)
    else { throw Failure(message: "cannot create png output") }
    CGImageDestinationAddImage(dest, image, nil)
    guard CGImageDestinationFinalize(dest) else { throw Failure(message: "cannot write png output") }
    return neutral
}

func write(_ image: CGImage, to path: String, format: String, quality: Double) throws {
    let type: UTType = .jpeg
    let url = URL(fileURLWithPath: path)
    guard let dest = CGImageDestinationCreateWithURL(url as CFURL, type.identifier as CFString, 1, nil) else {
        throw Failure(message: "cannot create \(format) output")
    }
    var props: [CFString: Any] = [:]
    if format == "jpeg" { props[kCGImageDestinationLossyCompressionQuality] = quality }
    CGImageDestinationAddImage(dest, image, props as CFDictionary)
    guard CGImageDestinationFinalize(dest) else { throw Failure(message: "cannot write \(format) output") }
}

/// The first page of a PDF on white, cropped to what is drawn on it.
func renderPDF(_ input: String, to output: String, dpi: Double) throws -> [String: Any] {
    guard let document = CGPDFDocument(URL(fileURLWithPath: input) as CFURL), let page = document.page(at: 1) else {
        throw Failure(message: "cannot open the drawing")
    }
    let box = page.getBoxRect(.mediaBox)
    let scale = dpi / 72
    let w = Int((box.width * scale).rounded()), h = Int((box.height * scale).rounded())
    guard w > 0, h > 0, w * h <= maxPixels, let srgb = CGColorSpace(name: CGColorSpace.sRGB),
          let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
                              space: srgb, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)
    else { throw Failure(message: "the drawing is too large") }
    ctx.setFillColor(CGColor(srgbRed: 1, green: 1, blue: 1, alpha: 1))
    ctx.fill(CGRect(x: 0, y: 0, width: w, height: h))
    ctx.scaleBy(x: scale, y: scale)
    ctx.translateBy(x: -box.minX, y: -box.minY)
    ctx.drawPDFPage(page)
    guard let data = ctx.data else { throw Failure(message: "cannot read the drawing") }
    let px = data.assumingMemoryBound(to: UInt8.self)
    let stride = ctx.bytesPerRow
    // Rows run from the top in memory. Anything not near-white is drawing.
    var x0 = w, y0 = h, x1 = -1, y1 = -1
    for y in 0..<h {
        let row = y * stride
        for x in 0..<w {
            let i = row + x * 4
            if px[i] < 247 || px[i + 1] < 247 || px[i + 2] < 247 {
                x0 = min(x0, x); x1 = max(x1, x); y0 = min(y0, y); y1 = max(y1, y)
            }
        }
    }
    guard x1 >= x0, y1 >= y0, let full = ctx.makeImage() else { throw Failure(message: "the drawing is empty") }
    let margin = Int(12 * scale)
    let crop = CGRect(x: max(0, x0 - margin), y: max(0, y0 - margin),
                      width: min(w, x1 + margin + 1) - max(0, x0 - margin), height: min(h, y1 + margin + 1) - max(0, y0 - margin))
    guard let image = full.cropping(to: crop),
          let dest = CGImageDestinationCreateWithURL(URL(fileURLWithPath: output) as CFURL, UTType.png.identifier as CFString, 1, nil)
    else { throw Failure(message: "cannot write the drawing") }
    CGImageDestinationAddImage(dest, image, nil)
    guard CGImageDestinationFinalize(dest) else { throw Failure(message: "cannot write the drawing") }
    return ["width": image.width, "height": image.height]
}

/// Every pass over a canvas: upright with and without correction, turned a
/// quarter each way, and the same over each tile when the request asks for tiles.
func readAll(_ canvas: Canvas, request: [String: Any]) throws -> [[String: Any]] {
    var passes = try [true, false].map { correction in
        ["rotation": 0, "correction": correction, "lines": try recognize(canvas, correction: correction)] as [String: Any]
    }
    let rotations = (request["rotations"] as? [Int] ?? []).filter { $0 == 90 || $0 == 270 }
    for degrees in rotations {
        let lines = try recognize(try turned(canvas, by: degrees), correction: true) {
            unturn($0, by: degrees, upright: canvas)
        }
        passes.append(["rotation": degrees, "correction": true, "lines": lines])
    }
    if let size = request["tile"] as? Int {
        for r in tileRects(width: canvas.width, height: canvas.height, size: size, overlap: request["overlap"] as? Int ?? size / 4) {
            try autoreleasepool {
                let tile = try piece(canvas, r)
                let rect = [Double(r.minX), Double(r.minY), Double(r.width), Double(r.height)]
                let shift: ([Double]) -> [Double] = { b in [b[0] + rect[0], b[1] + rect[1], b[2], b[3]] }
                for correction in [true, false] {
                    passes.append(["rotation": 0, "correction": correction, "tile": rect,
                                   "lines": try recognize(tile, correction: correction, map: shift)])
                }
                for degrees in rotations {
                    let lines = try recognize(try turned(tile, by: degrees), correction: true) {
                        shift(unturn($0, by: degrees, upright: tile))
                    }
                    passes.append(["rotation": degrees, "correction": true, "tile": rect, "lines": lines])
                }
            }
        }
    }
    return passes
}

/// The canvas enlarged `zoom` times, smoothly resampled, for reading small text.
func enlarged(_ canvas: Canvas, by zoom: Double) throws -> Canvas {
    let w = Int((Double(canvas.width) * zoom).rounded()), h = Int((Double(canvas.height) * zoom).rounded())
    guard w * h <= maxPixels, let srgb = CGColorSpace(name: CGColorSpace.sRGB),
          let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4, space: srgb,
                              bitmapInfo: canvas.context.bitmapInfo.rawValue)
    else { throw Failure(message: "the image is too large to enlarge") }
    ctx.interpolationQuality = .high
    ctx.draw(try canvas.image, in: CGRect(x: 0, y: 0, width: w, height: h))
    return Canvas(context: ctx, width: w, height: h, frames: canvas.frames, orientation: canvas.orientation,
                  hasAlpha: canvas.hasAlpha, type: canvas.type)
}

/// Passes read on an enlarged canvas, with every box back in the original's pixels.
func shrink(_ passes: [[String: Any]], by zoom: Double) -> [[String: Any]] {
    func box(_ value: Any?) -> Any {
        guard let b = value as? [Double] else { return value ?? NSNull() }
        return b.map { ($0 / zoom * 10).rounded() / 10 }
    }
    return passes.map { pass in
        var out = pass
        if pass["tile"] != nil { out["tile"] = box(pass["tile"]) }
        out["lines"] = (pass["lines"] as? [[String: Any]] ?? []).map { line in
            var shrunk = line
            shrunk["box"] = box(line["box"])
            shrunk["candidates"] = (line["candidates"] as? [[String: Any]] ?? []).map { candidate in
                var c = candidate
                c["chars"] = (candidate["chars"] as? [Any] ?? []).map { box($0) }
                return c
            }
            return shrunk
        }
        return out
    }
}

func handle(_ request: [String: Any]) throws -> [String: Any] {
    guard let command = request["command"] as? String else { throw Failure(message: "missing command") }
    guard let input = request["input"] as? String else { throw Failure(message: "missing input") }
    if command == "render-emf" {
        guard let output = request["output"] as? String else { throw Failure(message: "render-emf needs output") }
        return try renderEMF(input, to: output, dpi: request["dpi"] as? Double ?? 220)
    }
    if command == "pdf-read" {
        guard let output = request["output"] as? String else { throw Failure(message: "pdf-read needs output") }
        return try readPDF(input, output: output, dpi: request["dpi"] as? Double ?? 200,
                           maxPagePixels: request["max_pixels"] as? Double ?? 36_000_000,
                           ocrDPI: request["ocr_dpi"] as? Double, maxOCRPixels: request["max_ocr_pixels"] as? Double ?? 100_000_000)
    }
    if command == "pdf-text" {
        return try pdfText(input)
    }
    if command == "render-pdf" {
        guard let output = request["output"] as? String else { throw Failure(message: "render-pdf needs output") }
        return try renderPDF(input, to: output, dpi: request["dpi"] as? Double ?? 220)
    }
    let canvas = try open(input, orient: request["orientation"] as? Bool ?? true, crop: request["crop"] as? [Double] ?? [])
    switch command {
    case "regions":
        return try sensitiveRegions(canvas)
    case "probe":
        return describe(canvas)
    case "print":
        return try fingerprint(canvas)
    case "ocr":
        let zoom = max(1, request["zoom"] as? Double ?? 1)
        let passes = try readAll(zoom > 1 ? try enlarged(canvas, by: zoom) : canvas, request: request)
        return describe(canvas).merging(["passes": zoom > 1 ? shrink(passes, by: zoom) : passes]) { a, _ in a }
    case "fit":
        guard let boxes = request["boxes"] as? [[Double]] else { throw Failure(message: "fit needs boxes") }
        let margin = request["margin"] as? Int ?? 2
        let guards=request["guards"] as? [[[Double]]] ?? []
        return describe(canvas).merging(["boxes": boxes.enumerated().map { i,b in
            fit(canvas, box: b, margin: margin, guards: i<guards.count ? guards[i] : [])
        }]) { a, _ in a }
    case "mask":
        guard let output = request["output"] as? String,
              let format = request["format"] as? String, ["raw", "jpeg", "png"].contains(format),
              let boxes = request["boxes"] as? [[Double]]
        else { throw Failure(message: "mask needs output, format and boxes") }
        if format != "raw" && canvas.hasAlpha { throw Failure(message: "a \(format.uppercased()) page cannot keep transparency") }
        mask(canvas, boxes: boxes)
        if format == "raw" {
            try writeRaw(canvas, to: output)
        } else if format == "png" {
            let grey = try writePNG(canvas, to: output)
            return describe(canvas).merging(["masked": boxes.count, "grey": grey]) { a, _ in a }
        } else {
            try write(try canvas.image, to: output, format: format, quality: request["quality"] as? Double ?? 0.92)
        }
        return describe(canvas).merging(["masked": boxes.count]) { a, _ in a }
    default:
        throw Failure(message: "unknown command \(command)")
    }
}

var response: [String: Any]
do {
    let input = FileHandle.standardInput.readDataToEndOfFile()
    guard let request = try JSONSerialization.jsonObject(with: input) as? [String: Any] else {
        throw Failure(message: "request must be a JSON object")
    }
    response = ["ok": true].merging(try handle(request)) { a, _ in a }
} catch let failure as Failure {
    response = ["ok": false, "error": failure.message]
} catch {
    response = ["ok": false, "error": "\(error)"]
}
let data = (try? JSONSerialization.data(withJSONObject: response)) ?? Data(#"{"ok":false,"error":"cannot encode response"}"#.utf8)
FileHandle.standardOutput.write(data)
exit(response["ok"] as? Bool == true ? 0 : 1)
