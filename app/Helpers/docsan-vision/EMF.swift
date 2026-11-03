// EMF drawings (Visio diagrams and other vector pictures in Word files),
// drawn with Core Graphics. macOS has no EMF support, and LibreOffice cannot
// run inside the app's sandbox, so the helper replays the drawing records
// itself: shapes, paths, text and bitmaps, with the window, viewport and
// world transforms. Text is drawn and also reported with the box of every
// character, so the engine can match a drawing's words exactly instead of
// through OCR. Characters are placed where the drawing's own spacing puts
// them, so a substituted font cannot move a label out of its box. Records it
// does not draw are counted and reported, and a drawing kept only in EMF+
// (with no full GDI copy) is reported too: the engine refuses both rather
// than show an incomplete diagram.

import CoreGraphics
import CoreText
import Foundation
import ImageIO

struct EMFText {
    let text: String
    let box: CGRect  // pixels, origin top left
    let chars: [CGRect?]  // one per Unicode scalar
}

struct EMFResult {
    let image: CGImage
    let texts: [EMFText]
    let skipped: [Int: Int]  // record type -> count
    let emfPlusOnly: Bool  // the GDI records are not a full copy of the drawing
}

private struct Pen {
    var color = CGColor(srgbRed: 0, green: 0, blue: 0, alpha: 1)
    var width: Double = 0  // logical units; 0 is one device pixel
    var visible = true
    var dash: [CGFloat] = []
}

private struct Brush {
    var color = CGColor(srgbRed: 1, green: 1, blue: 1, alpha: 1)
    var visible = true
    var hatch: Int? = nil  // HS_HORIZONTAL ... HS_DIAGCROSS
    var pattern: CGImage? = nil
}

private struct Font {
    var height: Double = 12  // logical units
    var weight: Int = 400
    var italic = false
    var escapement: Double = 0  // tenths of a degree
    var face = "Helvetica"
}

private enum Object {
    case pen(Pen), brush(Brush), font(Font), other
}

private struct DC {
    var pen = Pen()
    var brush = Brush()
    var font = Font()
    var textColor = CGColor(srgbRed: 0, green: 0, blue: 0, alpha: 1)
    var bkColor = CGColor(srgbRed: 1, green: 1, blue: 1, alpha: 1)
    var opaque = false  // background mode OPAQUE: text and hatches get a background
    var clockwise = false  // arc direction
    var textAlign: UInt32 = 0
    var evenOdd = true
    var mapMode: UInt32 = 1
    var windowOrg = CGPoint.zero, windowExt = CGSize(width: 1, height: 1)
    var viewportOrg = CGPoint.zero, viewportExt = CGSize(width: 1, height: 1)
    var world = CGAffineTransform.identity
    var current = CGPoint.zero
}

private func color(_ ref: UInt32) -> CGColor {
    CGColor(srgbRed: Double(ref & 0xFF) / 255, green: Double((ref >> 8) & 0xFF) / 255,
            blue: Double((ref >> 16) & 0xFF) / 255, alpha: 1)
}

final class EMFRenderer {
    private let d: Data
    private var dc = DC()
    private var stack: [DC] = []
    private var objects: [UInt32: Object] = [:]
    private var path: CGMutablePath?
    private var ctx: CGContext!
    private var toOutput = CGAffineTransform.identity  // device units to output pixels (y down)
    private var height = 0
    private var texts: [EMFText] = []
    private var skipped: [Int: Int] = [:]
    private var emfPlusOnly = false
    private var pixel = 1.0  // output pixels per device pixel

    init(data: Data) { d = data }

    private func u32(_ o: Int) -> UInt32 {
        guard o + 4 <= d.count else { return 0 }
        return d.withUnsafeBytes { $0.loadUnaligned(fromByteOffset: o, as: UInt32.self) }
    }
    private func i32(_ o: Int) -> Int32 { Int32(bitPattern: u32(o)) }
    private func u16(_ o: Int) -> UInt16 { UInt16(bitPattern: i16(o)) }
    private func i16(_ o: Int) -> Int16 {
        guard o + 2 <= d.count else { return 0 }
        return d.withUnsafeBytes { $0.loadUnaligned(fromByteOffset: o, as: Int16.self) }
    }
    private func f32(_ o: Int) -> Double { Double(Float(bitPattern: u32(o))) }

    /// Logical coordinates to output pixels: world transform, then the mapping mode, then the picture frame.
    private func pageToDevice(_ p: CGPoint) -> CGPoint {
        switch dc.mapMode {
        case 7, 8:  // MM_ISOTROPIC, MM_ANISOTROPIC
            let sx = dc.windowExt.width == 0 ? 1 : dc.viewportExt.width / dc.windowExt.width
            let sy = dc.windowExt.height == 0 ? 1 : dc.viewportExt.height / dc.windowExt.height
            return CGPoint(x: (p.x - dc.windowOrg.x) * sx + dc.viewportOrg.x, y: (p.y - dc.windowOrg.y) * sy + dc.viewportOrg.y)
        case 2, 3, 4, 5, 6:  // LOMETRIC, HIMETRIC, LOENGLISH, HIENGLISH, TWIPS: physical units, y up
            let mm: Double = [2: 0.1, 3: 0.01, 4: 0.254, 5: 0.0254, 6: 25.4 / 1440][Int(dc.mapMode)] ?? 1
            let perMM = devicePerMM
            return CGPoint(x: (p.x - dc.windowOrg.x) * mm * perMM.width + dc.viewportOrg.x,
                           y: -(p.y - dc.windowOrg.y) * mm * perMM.height + dc.viewportOrg.y)
        default:  // MM_TEXT
            return CGPoint(x: p.x - dc.windowOrg.x + dc.viewportOrg.x, y: p.y - dc.windowOrg.y + dc.viewportOrg.y)
        }
    }
    private var devicePerMM = CGSize(width: 1, height: 1)

    private func pt(_ x: Double, _ y: Double) -> CGPoint {
        pageToDevice(CGPoint(x: x, y: y).applying(dc.world)).applying(toOutput)
    }

    /// How long one logical unit is in output pixels (for pen widths and font sizes).
    private func logicalScale() -> Double {
        let a = pt(0, 0), b = pt(0, 1000)
        return hypot(b.x - a.x, b.y - a.y) / 1000
    }

    /// The same along the x axis, for character spacing.
    private func logicalScaleX() -> Double {
        let a = pt(0, 0), b = pt(1000, 0)
        return hypot(b.x - a.x, b.y - a.y) / 1000
    }

    private func device(_ x: Double, _ y: Double) -> CGPoint { CGPoint(x: x, y: y).applying(toOutput) }

    func render(dpi: Double) throws -> EMFResult {
        guard d.count >= 88, u32(0) == 1, u32(40) == 0x464D4520 else { throw Failure(message: "not an EMF drawing") }
        // Header: bounds (device units), frame (0.01 mm), device size in pixels and millimetres.
        let frame = CGRect(x: Double(i32(24)), y: Double(i32(28)), width: Double(i32(32) - i32(24)), height: Double(i32(36) - i32(28)))
        let device = CGSize(width: Double(i32(72)), height: Double(i32(76)))
        let mm = CGSize(width: Double(i32(80)), height: Double(i32(84)))
        guard device.width > 0, device.height > 0, mm.width > 0, mm.height > 0, frame.width > 0, frame.height > 0 else {
            throw Failure(message: "the drawing has no size")
        }
        devicePerMM = CGSize(width: device.width / mm.width, height: device.height / mm.height)
        let w = Int((frame.width / 2540 * dpi).rounded()), h = Int((frame.height / 2540 * dpi).rounded())
        guard w > 0, h > 0, w * h <= maxPixels, let srgb = CGColorSpace(name: CGColorSpace.sRGB),
              let context = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
                                      space: srgb, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)
        else { throw Failure(message: "the drawing is too large") }
        ctx = context
        height = h
        ctx.setFillColor(CGColor(srgbRed: 1, green: 1, blue: 1, alpha: 1))
        ctx.fill(CGRect(x: 0, y: 0, width: w, height: h))
        // Draw in a top-left coordinate space.
        ctx.translateBy(x: 0, y: Double(h))
        ctx.scaleBy(x: 1, y: -1)
        // Device units to output pixels: millimetres per device unit, then the frame origin.
        let scaleX = dpi / 25.4 / devicePerMM.width, scaleY = dpi / 25.4 / devicePerMM.height
        let originX = frame.minX / 100 * devicePerMM.width, originY = frame.minY / 100 * devicePerMM.height
        toOutput = CGAffineTransform(a: scaleX, b: 0, c: 0, d: scaleY, tx: -originX * scaleX, ty: -originY * scaleY)
        pixel = max(scaleX, 0.0001)
        ctx.setLineCap(.round)
        ctx.setLineJoin(.round)

        var o = 0
        while o + 8 <= d.count {
            let type = Int(u32(o)), size = Int(u32(o + 4))
            guard size >= 8, o + size <= d.count else { break }
            if type == 14 { break }  // EMR_EOF
            record(type, o, size)
            o += size
        }
        guard let image = ctx.makeImage() else { throw Failure(message: "cannot draw the drawing") }
        return EMFResult(image: image, texts: texts, skipped: skipped, emfPlusOnly: emfPlusOnly)
    }

    // MARK: Records

    private func points32(_ o: Int, count: Int) -> [CGPoint] {
        (0..<count).map { pt(Double(i32(o + $0 * 8)), Double(i32(o + $0 * 8 + 4))) }
    }
    private func points16(_ o: Int, count: Int) -> [CGPoint] {
        (0..<count).map { pt(Double(i16(o + $0 * 4)), Double(i16(o + $0 * 4 + 2))) }
    }
    private func rect(_ o: Int) -> CGRect {
        let a = pt(Double(i32(o)), Double(i32(o + 4))), b = pt(Double(i32(o + 8)), Double(i32(o + 12)))
        return CGRect(x: min(a.x, b.x), y: min(a.y, b.y), width: abs(b.x - a.x), height: abs(b.y - a.y))
    }

    private func record(_ type: Int, _ o: Int, _ size: Int) {
        let b = o + 8  // record body
        switch type {
        case 1, 13, 16, 20, 21, 23, 28, 48, 49, 50, 51, 52, 58, 65, 98, 99, 100, 101, 104, 105, 106, 107, 109, 110, 111, 112, 113, 115, 119, 120, 121, 122:
            break  // the header, and settings, palettes and escapes that do not change what is drawn
        case 70:
            // A comment. An EMF+ header in one says whether the GDI records that
            // follow are a full copy of the drawing ("dual"); if not, they are a stub.
            if size >= 24, u32(b + 4) == 0x2B46_4D45, u16(b + 8) == 0x4001, u16(b + 10) & 1 == 0 {
                emfPlusOnly = true
            }
        case 9: dc.windowExt = CGSize(width: Double(i32(b)), height: Double(i32(b + 4)))
        case 10: dc.windowOrg = CGPoint(x: Double(i32(b)), y: Double(i32(b + 4)))
        case 11: dc.viewportExt = CGSize(width: Double(i32(b)), height: Double(i32(b + 4)))
        case 12: dc.viewportOrg = CGPoint(x: Double(i32(b)), y: Double(i32(b + 4)))
        case 31:  // EMR_SCALEVIEWPORTEXTEX
            let xn = Double(i32(b)), xd = Double(i32(b + 4)), yn = Double(i32(b + 8)), yd = Double(i32(b + 12))
            if xd != 0, yd != 0 { dc.viewportExt = CGSize(width: dc.viewportExt.width * xn / xd, height: dc.viewportExt.height * yn / yd) }
        case 32:  // EMR_SCALEWINDOWEXTEX
            let xn = Double(i32(b)), xd = Double(i32(b + 4)), yn = Double(i32(b + 8)), yd = Double(i32(b + 12))
            if xd != 0, yd != 0 { dc.windowExt = CGSize(width: dc.windowExt.width * xn / xd, height: dc.windowExt.height * yn / yd) }
        case 17: dc.mapMode = u32(b)
        case 18: dc.opaque = u32(b) == 2
        case 19: dc.evenOdd = u32(b) == 1
        case 22: dc.textAlign = u32(b)
        case 24: dc.textColor = color(u32(b))
        case 25: dc.bkColor = color(u32(b))
        case 57: dc.clockwise = u32(b) == 2
        case 35: dc.world = xform(b)
        case 36:
            let x = xform(b)
            switch u32(b + 24) {
            case 1: dc.world = .identity
            case 2: dc.world = x.concatenating(dc.world)
            case 3: dc.world = dc.world.concatenating(x)
            case 4: dc.world = x
            default: break
            }
        case 33:
            stack.append(dc)
            ctx.saveGState()
        case 34:
            let n = Int(i32(b))
            let pops = n < 0 ? min(-n, stack.count) : max(0, stack.count - n)
            for _ in 0..<pops where !stack.isEmpty {
                dc = stack.removeLast()
                ctx.restoreGState()
            }
        case 30:  // EMR_INTERSECTCLIPRECT
            ctx.clip(to: rect(b))
        case 75:  // EMR_EXTSELECTCLIPRGN, in device units
            let rects = regionRects(b + 8, size: Int(u32(b)), logical: false)
            switch u32(b + 4) {
            case 5:  // RGN_COPY
                ctx.resetClip()
                if !rects.isEmpty { ctx.clip(to: rects) }
            case 1:  // RGN_AND
                ctx.clip(to: rects.isEmpty ? [.zero] : rects)
            case 4:  // RGN_DIFF
                exclude(rects)
            default:  // RGN_OR and RGN_XOR can only widen the clip: draw everything
                ctx.resetClip()
            }
        case 29:  // EMR_EXCLUDECLIPRECT
            exclude([rect(b)])
        case 38:  // EMR_CREATEPEN
            var pen = Pen()
            let style = u32(b + 4)
            pen.visible = style & 0xF != 5
            pen.width = Double(i32(b + 8))
            pen.color = color(u32(b + 16))
            pen.dash = dashes(style & 0xF)
            objects[u32(b)] = .pen(pen)
        case 95:  // EMR_EXTCREATEPEN
            var pen = Pen()
            let elp = b + 20
            let style = u32(elp)
            pen.visible = style & 0xF != 5 && u32(elp + 8) != 1
            pen.width = Double(u32(elp + 4))
            pen.color = color(u32(elp + 12))
            pen.dash = dashes(style & 0xF)
            objects[u32(b)] = .pen(pen)
        case 39:  // EMR_CREATEBRUSHINDIRECT
            var brush = Brush()
            brush.visible = u32(b + 4) != 1
            brush.color = color(u32(b + 8))
            if u32(b + 4) == 2 { brush.hatch = Int(u32(b + 12)) }
            objects[u32(b)] = .brush(brush)
        case 93, 94:  // EMR_CREATEMONOBRUSH and EMR_CREATEDIBPATTERNBRUSHPT: the bitmap is tiled
            if let image = dib(o, bmi: Int(u32(b + 8)), bits: Int(u32(b + 16)), bitsSize: Int(u32(b + 20))) {
                objects[u32(b)] = .brush(Brush(color: CGColor(srgbRed: 0.5, green: 0.5, blue: 0.5, alpha: 1), visible: true, pattern: image))
            } else {
                skipped[type, default: 0] += 1
                objects[u32(b)] = .brush(Brush(color: CGColor(srgbRed: 0.75, green: 0.75, blue: 0.75, alpha: 1), visible: true))
            }
        case 82:  // EMR_EXTCREATEFONTINDIRECTW
            var font = Font()
            let lf = b + 4
            font.height = Double(abs(i32(lf)))
            font.escapement = Double(i32(lf + 8))
            font.weight = Int(i32(lf + 16))
            font.italic = d.count > lf + 20 && d[lf + 20] != 0
            let face = (0..<32).map { UInt16(bitPattern: i16(lf + 28 + $0 * 2)) }.prefix { $0 != 0 }
            let name = String(decoding: Array(face), as: UTF16.self)
            if !name.isEmpty { font.face = name }
            if font.height == 0 { font.height = 12 }
            objects[u32(b)] = .font(font)
        case 37:  // EMR_SELECTOBJECT
            select(u32(b))
        case 40: objects[u32(b)] = nil
        case 27: dc.current = CGPoint(x: Double(i32(b)), y: Double(i32(b + 4)))
            if let path { path.move(to: pt(dc.current.x, dc.current.y)) }
        case 54:  // EMR_LINETO
            let p = CGPoint(x: Double(i32(b)), y: Double(i32(b + 4)))
            let line = CGMutablePath()
            line.move(to: pt(dc.current.x, dc.current.y))
            line.addLine(to: pt(p.x, p.y))
            emit(line, fill: false, continues: true)
            dc.current = p
        case 3, 86: polygon(points(type == 86, b + 16, count: Int(u32(b + 16))), closed: true)
        case 4, 87: polygon(points(type == 87, b + 16, count: Int(u32(b + 16))), closed: false)
        case 6, 89:  // POLYLINETO
            let ps = points(type == 89, b + 16, count: Int(u32(b + 16)))
            let line = CGMutablePath()
            line.move(to: pt(dc.current.x, dc.current.y))
            for p in ps { line.addLine(to: p) }
            emit(line, fill: false, continues: true)
            if let last = lastLogical(type == 89, b + 16, count: Int(u32(b + 16))) { dc.current = last }
        case 2, 85, 5, 88:  // POLYBEZIER and POLYBEZIERTO
            let to = type == 5 || type == 88
            let ps = points(type == 85 || type == 88, b + 16, count: Int(u32(b + 16)))
            let curve = CGMutablePath()
            var rest = ps[...]
            if to {
                curve.move(to: pt(dc.current.x, dc.current.y))
            } else if let first = rest.first {
                curve.move(to: first)
                rest = rest.dropFirst()
            }
            var points = Array(rest)
            while points.count >= 3 {
                curve.addCurve(to: points[2], control1: points[0], control2: points[1])
                points.removeFirst(3)
            }
            emit(curve, fill: false, continues: to)
            if to, let last = lastLogical(type == 88, b + 16, count: Int(u32(b + 16))) { dc.current = last }
        case 7, 8, 90, 91:  // POLYPOLYLINE and POLYPOLYGON
            let short = type == 90 || type == 91
            let polys = Int(u32(b + 16)), counts = b + 24
            var at = counts + polys * 4
            let shape = CGMutablePath()
            for i in 0..<polys {
                let n = Int(u32(counts + i * 4))
                let ps = points(short, at, count: n, raw: true)
                if let first = ps.first {
                    shape.move(to: first)
                    for p in ps.dropFirst() { shape.addLine(to: p) }
                    if type == 8 || type == 91 { shape.closeSubpath() }
                }
                at += n * (short ? 4 : 8)
            }
            emit(shape, fill: type == 8 || type == 91)
        case 43:  // EMR_RECTANGLE
            let r = rect(b)
            emit(CGPath(rect: r, transform: nil), fill: true)
        case 44:  // EMR_ROUNDRECT
            let r = rect(b)
            let corner = abs(Double(i32(b + 16))) * logicalScale() / 2
            emit(CGPath(roundedRect: r, cornerWidth: min(corner, r.width / 2), cornerHeight: min(corner, r.height / 2), transform: nil), fill: true)
        case 42:  // EMR_ELLIPSE
            emit(CGPath(ellipseIn: rect(b), transform: nil), fill: true)
        case 45, 46, 47, 55:  // ARC, CHORD, PIE, ARCTO: drawn as the ellipse's outline segment
            let r = rect(b)
            let c = CGPoint(x: r.midX, y: r.midY)
            let s = pt(Double(i32(b + 16)), Double(i32(b + 20))), e = pt(Double(i32(b + 24)), Double(i32(b + 28)))
            let a0 = atan2(s.y - c.y, s.x - c.x), a1 = atan2(e.y - c.y, e.x - c.x)
            let arc = CGMutablePath()
            let t = CGAffineTransform(translationX: c.x, y: c.y).scaledBy(x: r.width / 2, y: r.height / 2)
            if type == 47 { arc.move(to: c) }
            arc.addArc(center: .zero, radius: 1, startAngle: a0, endAngle: a1, clockwise: true, transform: t)
            if type == 46 || type == 47 { arc.closeSubpath() }
            emit(arc, fill: type == 46 || type == 47, continues: type == 55)
        case 15:  // EMR_SETPIXELV
            let p = pt(Double(i32(b)), Double(i32(b + 4)))
            ctx.setFillColor(color(u32(b + 8)))
            ctx.fill(CGRect(x: p.x, y: p.y, width: max(1, pixel), height: max(1, pixel)))
        case 41:  // EMR_ANGLEARC: a line to the arc's start, then the arc
            let cx = Double(i32(b)), cy = Double(i32(b + 4)), r = Double(u32(b + 8))
            let a0 = f32(b + 12) * .pi / 180, sweep = f32(b + 16) * .pi / 180
            let line = CGMutablePath()
            line.move(to: pt(dc.current.x, dc.current.y))
            for i in 0...64 {
                let a = a0 + sweep * Double(i) / 64
                line.addLine(to: pt(cx + r * cos(a), cy - r * sin(a)))
            }
            emit(line, fill: false, continues: true)
            dc.current = CGPoint(x: cx + r * cos(a0 + sweep), y: cy - r * sin(a0 + sweep))
        case 56, 92:  // EMR_POLYDRAW and POLYDRAW16
            let short = type == 92
            let n = Int(u32(b + 16))
            let ps = points(short, b + 16, count: n)
            let kinds = b + 20 + n * (short ? 4 : 8)
            let shape = CGMutablePath()
            shape.move(to: pt(dc.current.x, dc.current.y))
            var i = 0
            while i < ps.count, kinds + i < d.count {
                let t = d[kinds + i]
                switch t & 0xFE {
                case 6: shape.move(to: ps[i])
                case 2: shape.addLine(to: ps[i])
                case 4 where i + 2 < ps.count:
                    shape.addCurve(to: ps[i + 2], control1: ps[i], control2: ps[i + 1])
                    i += 2
                default: break
                }
                if t & 1 != 0 { shape.closeSubpath() }
                i += 1
            }
            emit(shape, fill: false, continues: true)
            if let last = lastLogical(short, b + 16, count: n) { dc.current = last }
        case 66:  // EMR_WIDENPATH: the path becomes the outline its pen would draw
            if let p = path {
                path = p.copy(strokingWithWidth: max(1, dc.pen.width * logicalScale()), lineCap: .round, lineJoin: .round, miterLimit: 10).mutableCopy()
            }
        case 71:  // EMR_FILLRGN
            if case .brush(let brush)? = objects[u32(b + 20)] { fillRects(regionRects(b + 24, size: Int(u32(b + 16)), logical: true), brush) }
            else { fillRects(regionRects(b + 24, size: Int(u32(b + 16)), logical: true), dc.brush) }
        case 72:  // EMR_FRAMERGN
            var brush = dc.brush
            if case .brush(let chosen)? = objects[u32(b + 20)] { brush = chosen }
            let width = max(1, Double(i32(b + 24)) * logicalScale())
            ctx.setStrokeColor(brush.color)
            ctx.setLineWidth(width)
            for r in regionRects(b + 32, size: Int(u32(b + 16)), logical: true) { ctx.stroke(r.insetBy(dx: width / 2, dy: width / 2)) }
        case 74:  // EMR_PAINTRGN
            fillRects(regionRects(b + 20, size: Int(u32(b + 16)), logical: true), dc.brush)
        case 108:  // EMR_SMALLTEXTOUT
            smallText(b, recordStart: o, size: size)
        case 118:  // EMR_GRADIENTFILL
            gradient(b, end: o + size)
        case 59: path = CGMutablePath()
        case 60: break
        case 61: path?.closeSubpath()
        case 68: path = nil
        case 62, 63, 64:  // FILLPATH, STROKEANDFILLPATH, STROKEPATH
            if let p = path {
                path = nil
                draw(p, fill: type != 64, stroke: type != 62)
            }
        case 67:  // EMR_SELECTCLIPPATH
            if let p = path {
                path = nil
                if u32(b) == 5 { ctx.resetClip() }  // RGN_COPY replaces the clip
                if u32(b) == 4 {
                    exclude([], path: p)
                } else {
                    ctx.addPath(p)
                    ctx.clip(using: dc.evenOdd ? .evenOdd : .winding)
                }
            }
        case 84, 83:  // EMR_EXTTEXTOUTW and EXTTEXTOUTA
            text(b, wide: type == 84, recordStart: o)
        case 81:  // EMR_STRETCHDIBITS
            let dest = CGRect(x: Double(i32(b + 16)), y: Double(i32(b + 20)), width: Double(i32(b + 64)), height: Double(i32(b + 68)))
            bitmap(o, bmi: Int(u32(b + 40)), bits: Int(u32(b + 48)), bitsSize: Int(u32(b + 52)), dest: dest)
        case 76, 77, 114, 116:  // BITBLT, STRETCHBLT, ALPHABLEND, TRANSPARENTBLT: one layout
            let dest = CGRect(x: Double(i32(b + 16)), y: Double(i32(b + 20)), width: Double(i32(b + 24)), height: Double(i32(b + 28)))
            if u32(b + 80) == 0 {  // no bitmap: the raster operation fills the rectangle
                switch u32(b + 32) {
                case 0x0000_0042: ctx.setFillColor(color(0)); ctx.fill(rect(logical: dest))  // BLACKNESS
                case 0x00FF_0062: ctx.setFillColor(color(0xFFFFFF)); ctx.fill(rect(logical: dest))  // WHITENESS
                case 0x00AA_0029: break  // leaves the picture as it is
                case 0x00F0_0021: fill(CGPath(rect: rect(logical: dest), transform: nil), dc.brush)  // PATCOPY
                default: skipped[type, default: 0] += 1  // inverting operations
                }
            } else {
                bitmap(o, bmi: Int(u32(b + 76)), bits: Int(u32(b + 84)), bitsSize: Int(u32(b + 88)), dest: dest)
            }
        case 80:  // EMR_SETDIBITSTODEVICE: the source size is the destination size
            let dest = CGRect(x: Double(i32(b + 16)), y: Double(i32(b + 20)), width: Double(i32(b + 32)), height: Double(i32(b + 36)))
            bitmap(o, bmi: Int(u32(b + 40)), bits: Int(u32(b + 48)), bitsSize: Int(u32(b + 52)), dest: dest)
        default:
            skipped[type, default: 0] += 1
        }
    }

    private func xform(_ o: Int) -> CGAffineTransform {
        CGAffineTransform(a: f32(o), b: f32(o + 4), c: f32(o + 8), d: f32(o + 12), tx: f32(o + 16), ty: f32(o + 20))
    }

    private func dashes(_ style: UInt32) -> [CGFloat] {
        switch style {
        case 1: [18, 6]
        case 2: [3, 3]
        case 3: [9, 6, 3, 6]
        case 4: [9, 3, 3, 3, 3, 3]
        default: []
        }
    }

    private func select(_ handle: UInt32) {
        if handle & 0x8000_0000 != 0 {
            switch handle & 0x7FFF_FFFF {
            case 0: dc.brush = Brush(color: color(0xFFFFFF), visible: true)
            case 1: dc.brush = Brush(color: color(0xC0C0C0), visible: true)
            case 2: dc.brush = Brush(color: color(0x808080), visible: true)
            case 3: dc.brush = Brush(color: color(0x404040), visible: true)
            case 4: dc.brush = Brush(color: color(0), visible: true)
            case 5: dc.brush.visible = false
            case 6: dc.pen = Pen(color: color(0xFFFFFF))
            case 7: dc.pen = Pen()
            case 8: dc.pen.visible = false
            case 10, 11, 12, 13, 14, 16, 17: dc.font = Font()
            default: break
            }
            return
        }
        switch objects[handle] {
        case .pen(let p): dc.pen = p
        case .brush(let b): dc.brush = b
        case .font(let f): dc.font = f
        default: break
        }
    }

    private func points(_ short: Bool, _ o: Int, count: Int, raw: Bool = false) -> [CGPoint] {
        let start = raw ? o : o + 4
        let n = min(count, max(0, (d.count - start) / (short ? 4 : 8)))
        return short ? points16(start, count: n) : points32(start, count: n)
    }

    private func lastLogical(_ short: Bool, _ o: Int, count: Int) -> CGPoint? {
        guard count > 0 else { return nil }
        let at = o + 4 + (count - 1) * (short ? 4 : 8)
        return short ? CGPoint(x: Double(i16(at)), y: Double(i16(at + 2))) : CGPoint(x: Double(i32(at)), y: Double(i32(at + 4)))
    }

    private func rect(logical r: CGRect) -> CGRect {
        let a = pt(r.minX, r.minY), b = pt(r.maxX, r.maxY)
        return CGRect(x: min(a.x, b.x), y: min(a.y, b.y), width: abs(b.x - a.x), height: abs(b.y - a.y))
    }

    private func polygon(_ ps: [CGPoint], closed: Bool) {
        guard let first = ps.first else { return }
        let shape = CGMutablePath()
        shape.move(to: first)
        for p in ps.dropFirst() { shape.addLine(to: p) }
        if closed { shape.closeSubpath() }
        emit(shape, fill: closed)
    }

    /// Inside BEGINPATH..ENDPATH a shape is added to the path; otherwise it is drawn.
    private func emit(_ shape: CGPath, fill: Bool, continues: Bool = false) {
        if let path {
            if continues, !path.isEmpty {
                // Lines that continue from the current point join the open figure.
                shape.applyWithBlock { element in
                    let p = element.pointee
                    switch p.type {
                    case .moveToPoint: if path.isEmpty { path.move(to: p.points[0]) }
                    case .addLineToPoint: path.addLine(to: p.points[0])
                    case .addQuadCurveToPoint: path.addQuadCurve(to: p.points[1], control: p.points[0])
                    case .addCurveToPoint: path.addCurve(to: p.points[2], control1: p.points[0], control2: p.points[1])
                    case .closeSubpath: path.closeSubpath()
                    @unknown default: break
                    }
                }
            } else {
                path.addPath(shape)
            }
            return
        }
        draw(shape, fill: fill, stroke: true)
    }

    private func draw(_ shape: CGPath, fill: Bool, stroke: Bool) {
        if fill && dc.brush.visible {
            self.fill(shape, dc.brush)
        }
        if stroke && dc.pen.visible {
            ctx.addPath(shape)
            ctx.setStrokeColor(dc.pen.color)
            ctx.setLineWidth(max(1, dc.pen.width * logicalScale()))
            ctx.setLineDash(phase: 0, lengths: dc.pen.dash.map { $0 * max(1, dc.pen.width * logicalScale()) })
            ctx.strokePath()
        }
    }

    /// Fills a shape with a solid, hatched or bitmap brush.
    private func fill(_ shape: CGPath, _ brush: Brush) {
        let rule: CGPathFillRule = dc.evenOdd ? .evenOdd : .winding
        guard brush.hatch != nil || brush.pattern != nil else {
            ctx.addPath(shape)
            ctx.setFillColor(brush.color)
            ctx.fillPath(using: rule)
            return
        }
        ctx.saveGState()
        ctx.addPath(shape)
        ctx.clip(using: rule)
        let box = shape.boundingBoxOfPath
        if let image = brush.pattern {
            ctx.draw(image, in: CGRect(x: 0, y: 0, width: Double(image.width) * pixel, height: Double(image.height) * pixel), byTiling: true)
        } else if let hatch = brush.hatch {
            if dc.opaque {
                ctx.setFillColor(dc.bkColor)
                ctx.fill(box)
            }
            let step = 8 * pixel, span = box.width + box.height
            ctx.setStrokeColor(brush.color)
            ctx.setLineWidth(max(1, pixel))
            let lines = CGMutablePath()
            var t = -span
            while t <= span {
                if hatch == 0 || hatch == 4 { lines.move(to: CGPoint(x: box.minX, y: box.minY + t)); lines.addLine(to: CGPoint(x: box.maxX, y: box.minY + t)) }
                if hatch == 1 || hatch == 4 { lines.move(to: CGPoint(x: box.minX + t, y: box.minY)); lines.addLine(to: CGPoint(x: box.minX + t, y: box.maxY)) }
                if hatch == 2 || hatch == 5 { lines.move(to: CGPoint(x: box.minX + t, y: box.minY)); lines.addLine(to: CGPoint(x: box.minX + t + span, y: box.minY + span)) }
                if hatch == 3 || hatch == 5 { lines.move(to: CGPoint(x: box.minX + t, y: box.maxY)); lines.addLine(to: CGPoint(x: box.minX + t + span, y: box.maxY - span)) }
                t += step
            }
            ctx.addPath(lines)
            ctx.strokePath()
        }
        ctx.restoreGState()
    }

    private func fillRects(_ rects: [CGRect], _ brush: Brush) {
        guard !rects.isEmpty, brush.visible else { return }
        let shape = CGMutablePath()
        for r in rects { shape.addRect(r) }
        fill(shape, brush)
    }

    /// Clips away the rectangles (or a path): what lies inside is not drawn.
    private func exclude(_ rects: [CGRect], path: CGPath? = nil) {
        let everything = CGRect(x: -1e7, y: -1e7, width: 2e7, height: 2e7)
        ctx.addRect(everything)
        for r in rects { ctx.addRect(r) }
        if let path { ctx.addPath(path) }
        ctx.clip(using: .evenOdd)
    }

    /// The rectangles of a region (RGNDATA), in output pixels.
    private func regionRects(_ at: Int, size: Int, logical: Bool) -> [CGRect] {
        guard size >= 32, at + 32 <= d.count else { return [] }
        let n = Int(u32(at + 8))
        guard n >= 0, n < 1_000_000, at + 32 + n * 16 <= d.count else { return [] }
        return (0..<n).map { i in
            let r = at + 32 + i * 16
            if logical { return rect(r) }
            let a = device(Double(i32(r)), Double(i32(r + 4))), c = device(Double(i32(r + 8)), Double(i32(r + 12)))
            return CGRect(x: min(a.x, c.x), y: min(a.y, c.y), width: abs(c.x - a.x), height: abs(c.y - a.y))
        }
    }

    /// EMR_GRADIENTFILL: rectangles shade from one corner's colour to the other's;
    /// triangles take the average of their three corners.
    private func gradient(_ b: Int, end: Int) {
        let vertices = Int(u32(b + 16)), shapes = Int(u32(b + 20)), mode = u32(b + 24)
        let base = b + 28, index = base + vertices * 16
        guard vertices > 0, vertices < 100_000, shapes < 100_000, index + shapes * (mode == 2 ? 12 : 8) <= end,
              let srgb = CGColorSpace(name: CGColorSpace.sRGB) else {
            skipped[118, default: 0] += 1
            return
        }
        func vertex(_ i: Int) -> (CGPoint, [CGFloat]) {
            let v = base + i * 16
            let rgb = [u16(v + 8), u16(v + 10), u16(v + 12)].map { CGFloat($0 >> 8) / 255 }
            return (pt(Double(i32(v)), Double(i32(v + 4))), rgb)
        }
        for s in 0..<shapes {
            if mode == 2 {
                let ids = (0..<3).map { Int(u32(index + s * 12 + $0 * 4)) }
                guard ids.allSatisfy({ $0 < vertices }) else { continue }
                let corners = ids.map(vertex)
                let triangle = CGMutablePath()
                triangle.addLines(between: corners.map(\.0))
                triangle.closeSubpath()
                let avg = (0..<3).map { c in corners.map { $0.1[c] }.reduce(0, +) / 3 }
                ctx.addPath(triangle)
                ctx.setFillColor(CGColor(srgbRed: avg[0], green: avg[1], blue: avg[2], alpha: 1))
                ctx.fillPath()
            } else {
                let a = Int(u32(index + s * 8)), z = Int(u32(index + s * 8 + 4))
                guard a < vertices, z < vertices else { continue }
                let (p0, c0) = vertex(a), (p1, c1) = vertex(z)
                let r = CGRect(x: min(p0.x, p1.x), y: min(p0.y, p1.y), width: abs(p1.x - p0.x), height: abs(p1.y - p0.y))
                guard let shade = CGGradient(colorSpace: srgb, colorComponents: c0 + [1] + c1 + [1], locations: [0, 1], count: 2) else { continue }
                ctx.saveGState()
                ctx.clip(to: r)
                let start = mode == 0 ? CGPoint(x: p0.x, y: r.midY) : CGPoint(x: r.midX, y: p0.y)
                let stop = mode == 0 ? CGPoint(x: p1.x, y: r.midY) : CGPoint(x: r.midX, y: p1.y)
                ctx.drawLinearGradient(shade, start: start, end: stop, options: [.drawsBeforeStartLocation, .drawsAfterEndLocation])
                ctx.restoreGState()
            }
        }
    }

    private func text(_ b: Int, wide: Bool, recordStart o: Int) {
        let emr = b + 28  // after bounds, graphics mode and the two scales
        let ref = CGPoint(x: Double(i32(emr)), y: Double(i32(emr + 4)))
        let n = Int(u32(emr + 8)), offString = Int(u32(emr + 12)), options = u32(emr + 16)
        let start = o + offString
        guard n > 0, n < 100_000, start >= 0, start + n * (wide ? 2 : 1) <= d.count else { return }
        let units: [UInt16] = wide ? (0..<n).map { u16(start + $0 * 2) } : (0..<n).map { UInt16(d[start + $0]) }
        // The drawing's own spacing: one advance per character, in logical units.
        var advances: [Double]? = nil
        let offDx = Int(u32(emr + 36)), step = (options & 0x2000) != 0 ? 8 : 4
        if offDx > 0, o + offDx + n * step <= d.count {
            advances = (0..<n).map { Double(i32(o + offDx + $0 * step)) }
        }
        let box = CGRect(x: Double(i32(emr + 20)), y: Double(i32(emr + 24)), width: Double(i32(emr + 28) - i32(emr + 20)),
                         height: Double(i32(emr + 32) - i32(emr + 24)))
        draw(text: units, at: ref, advances: advances, opaque: (options & 0x2) != 0 ? box : nil, clip: (options & 0x4) != 0 ? box : nil)
    }

    /// EMR_SMALLTEXTOUT: text without a spacing array, 8- or 16-bit characters.
    private func smallText(_ b: Int, recordStart o: Int, size: Int) {
        let ref = CGPoint(x: Double(i32(b)), y: Double(i32(b + 4)))
        let n = Int(u32(b + 8)), options = u32(b + 12)
        let at = b + 28 + ((options & 0x100) != 0 ? 0 : 16)  // ETO_NO_RECT leaves out the bounds
        let small = (options & 0x200) != 0
        guard n > 0, n < 100_000, at + n * (small ? 1 : 2) <= o + size else { return }
        let units: [UInt16] = small ? (0..<n).map { UInt16(d[at + $0]) } : (0..<n).map { u16(at + $0 * 2) }
        draw(text: units, at: ref, advances: nil, opaque: nil, clip: nil)
    }

    private func draw(text units: [UInt16], at ref: CGPoint, advances: [Double]?, opaque: CGRect?, clip: CGRect?) {
        let string = String(decoding: units, as: UTF16.self).replacingOccurrences(of: "\0", with: "")
        if let opaque, opaque.width > 0, opaque.height > 0 {
            ctx.setFillColor(dc.bkColor)
            ctx.fill(rect(logical: opaque))
        }
        guard !string.trimmingCharacters(in: .whitespaces).isEmpty else { return }
        let size = max(1, dc.font.height * logicalScale())
        var traits: CTFontSymbolicTraits = []
        if dc.font.weight >= 600 { traits.insert(.boldTrait) }
        if dc.font.italic { traits.insert(.italicTrait) }
        var font = CTFontCreateWithName(dc.font.face as CFString, size, nil)
        if !traits.isEmpty, let styled = CTFontCreateCopyWithSymbolicTraits(font, size, nil, traits, traits) { font = styled }
        let ascent = CTFontGetAscent(font), descent = CTFontGetDescent(font)
        // Where each UTF-16 unit starts along the baseline, in output pixels.
        var offsets: [Double] = []
        var width = 0.0
        if let advances, advances.count == units.count {
            let sx = logicalScaleX()
            for a in advances {
                offsets.append(width)
                width += a * sx
            }
        } else {
            let attrs: [NSAttributedString.Key: Any] = [NSAttributedString.Key(kCTFontAttributeName as String): font]
            let line = CTLineCreateWithAttributedString(NSAttributedString(string: String(decoding: units, as: UTF16.self), attributes: attrs))
            offsets = (0..<units.count).map { Double(CTLineGetOffsetForStringIndex(line, $0, nil)) }
            width = CTLineGetTypographicBounds(line, nil, nil, nil)
        }
        let anchor = (dc.textAlign & 1) != 0 ? pt(dc.current.x, dc.current.y) : pt(ref.x, ref.y)
        let horizontal = dc.textAlign & 6
        let vertical = dc.textAlign & 24
        var dx = 0.0
        if horizontal == 6 { dx = -width / 2 } else if horizontal == 2 { dx = -width }
        var baseline = 0.0  // offset from the anchor down to the baseline
        if vertical == 0 { baseline = ascent } else if vertical == 8 { baseline = -descent }
        let angle = -dc.font.escapement / 10 * .pi / 180  // counter-clockwise in a y-down space
        let place = CGAffineTransform(translationX: anchor.x, y: anchor.y).rotated(by: angle).translatedBy(x: dx, y: baseline)
        func box(_ x0: Double, _ x1: Double) -> CGRect {
            CGRect(x: x0, y: -ascent, width: max(x1 - x0, 0.5), height: ascent + descent).applying(place)
        }
        ctx.saveGState()
        if let clip, clip.width > 0, clip.height > 0 { ctx.clip(to: rect(logical: clip)) }
        if dc.opaque {
            ctx.setFillColor(dc.bkColor)
            ctx.addPath(CGPath(rect: CGRect(x: 0, y: -ascent, width: width, height: ascent + descent), transform: [place]))
            ctx.fillPath()
        }
        ctx.concatenate(place)
        ctx.textMatrix = CGAffineTransform(scaleX: 1, y: -1)
        ctx.setFillColor(dc.textColor)
        // Each character at its own place, in the drawing's font or, where that
        // font has no glyph for it, in a font that has.
        var i = 0
        while i < units.count {
            let pair = i + 1 < units.count && UTF16.isLeadSurrogate(units[i]) && UTF16.isTrailSurrogate(units[i + 1])
            var chars = pair ? [units[i], units[i + 1]] : [units[i]]
            var glyphs = [CGGlyph](repeating: 0, count: chars.count)
            var glyphFont = font
            if !CTFontGetGlyphsForCharacters(font, &chars, &glyphs, chars.count) {
                glyphFont = CTFontCreateForString(font, String(decoding: chars, as: UTF16.self) as CFString, CFRange(location: 0, length: chars.count))
                _ = CTFontGetGlyphsForCharacters(glyphFont, &chars, &glyphs, chars.count)
            }
            if glyphs[0] != 0 {
                var position = CGPoint(x: offsets[i], y: 0)
                CTFontDrawGlyphs(glyphFont, &glyphs, &position, 1, ctx)
            }
            i += pair ? 2 : 1
        }
        ctx.restoreGState()
        // Character boxes, one per Unicode scalar, in top-left output pixels.
        var chars: [CGRect?] = []
        var unit = 0
        let clean = String(decoding: units, as: UTF16.self)
        for scalar in clean.unicodeScalars {
            let len = scalar.utf16.count
            let x0 = offsets[min(unit, offsets.count - 1)]
            let x1 = unit + len < offsets.count ? offsets[unit + len] : width
            if scalar.value != 0 {
                chars.append(CharacterSet.whitespaces.contains(scalar) ? nil : box(x0, x1))
            }
            unit += len
        }
        texts.append(EMFText(text: string, box: box(0, width), chars: chars))
        if (dc.textAlign & 1) != 0 {
            dc.current = CGPoint(x: dc.current.x + width / max(logicalScaleX(), 0.0001), y: dc.current.y)
        }
    }

    /// A device-independent bitmap stored in a record, as an image.
    private func dib(_ o: Int, bmi: Int, bits: Int, bitsSize: Int) -> CGImage? {
        guard bmi > 0, bits > 0, o + bmi + 40 <= d.count, o + bits + bitsSize <= d.count else { return nil }
        let h = o + bmi
        let width = Int(i32(h + 4)), rawHeight = Int(i32(h + 8))
        let bitCount = Int(u16(h + 14)), compression = u32(h + 16)
        let headerSize = Int(u32(h))
        let pixels = d[(o + bits)..<(o + bits + bitsSize)]
        if compression == 4 || compression == 5 {  // embedded JPEG or PNG
            guard let source = CGImageSourceCreateWithData(Data(pixels) as CFData, nil) else { return nil }
            return CGImageSourceCreateImageAtIndex(source, 0, nil)
        }
        guard width > 0, rawHeight != 0, [1, 4, 8, 16, 24, 32].contains(bitCount), compression == 0 || compression == 3 else { return nil }
        let height = abs(rawHeight)
        let stride = ((width * bitCount + 31) / 32) * 4
        guard stride * height <= pixels.count, width * height <= maxPixels else { return nil }
        var palette: [UInt32] = []
        if bitCount <= 8 {
            let colors = Int(u32(h + 32)) == 0 ? 1 << bitCount : Int(u32(h + 32))
            palette = (0..<min(colors, 256)).map { u32(h + headerSize + $0 * 4) }
        }
        var rgba = [UInt8](repeating: 255, count: width * height * 4)
        let base = pixels.startIndex
        for y in 0..<height {
            let srcRow = rawHeight > 0 ? height - 1 - y : y
            let row = base + srcRow * stride
            for x in 0..<width {
                var r = 0, g = 0, b = 0
                switch bitCount {
                case 24, 32:
                    let i = row + x * (bitCount / 8)
                    b = Int(pixels[i]); g = Int(pixels[i + 1]); r = Int(pixels[i + 2])
                case 16:
                    let v = Int(pixels[row + x * 2]) | Int(pixels[row + x * 2 + 1]) << 8
                    r = (v >> 10 & 31) * 255 / 31; g = (v >> 5 & 31) * 255 / 31; b = (v & 31) * 255 / 31
                default:
                    let bitIndex = x * bitCount
                    let byte = Int(pixels[row + bitIndex / 8])
                    let shift = 8 - bitCount - (bitIndex % 8)
                    let index = (byte >> shift) & ((1 << bitCount) - 1)
                    let c = index < palette.count ? palette[index] : 0
                    b = Int(c & 0xFF); g = Int(c >> 8 & 0xFF); r = Int(c >> 16 & 0xFF)
                }
                let o4 = (y * width + x) * 4
                rgba[o4] = UInt8(r); rgba[o4 + 1] = UInt8(g); rgba[o4 + 2] = UInt8(b)
            }
        }
        guard let provider = CGDataProvider(data: Data(rgba) as CFData), let srgb = CGColorSpace(name: CGColorSpace.sRGB) else { return nil }
        return CGImage(width: width, height: height, bitsPerComponent: 8, bitsPerPixel: 32, bytesPerRow: width * 4,
                       space: srgb, bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.noneSkipLast.rawValue),
                       provider: provider, decode: nil, shouldInterpolate: true, intent: .defaultIntent)
    }

    private func bitmap(_ o: Int, bmi: Int, bits: Int, bitsSize: Int, dest: CGRect) {
        guard let image = dib(o, bmi: bmi, bits: bits, bitsSize: bitsSize) else {
            skipped[81, default: 0] += 1
            return
        }
        let r = rect(logical: dest)
        // The context draws top-down here; images draw bottom-up, so flip for the image.
        ctx.saveGState()
        ctx.translateBy(x: r.minX, y: r.maxY)
        ctx.scaleBy(x: 1, y: -1)
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: r.width, height: r.height))
        ctx.restoreGState()
    }
}

/// The "render-emf" command: draw the EMF, write a PNG, report the text.
func renderEMF(_ input: String, to output: String, dpi: Double) throws -> [String: Any] {
    let data = try Data(contentsOf: URL(fileURLWithPath: input))
    let result = try EMFRenderer(data: data).render(dpi: dpi)
    guard let dest = CGImageDestinationCreateWithURL(URL(fileURLWithPath: output) as CFURL, "public.png" as CFString, 1, nil) else {
        throw Failure(message: "cannot write the drawing")
    }
    CGImageDestinationAddImage(dest, result.image, nil)
    guard CGImageDestinationFinalize(dest) else { throw Failure(message: "cannot write the drawing") }
    func r(_ b: CGRect) -> [Double] {
        [Double(b.minX), Double(b.minY), Double(b.width), Double(b.height)].map { (v: Double) -> Double in (v * 10).rounded() / 10 }
    }
    return [
        "width": result.image.width,
        "height": result.image.height,
        "texts": result.texts.map { t in
            ["text": t.text, "box": r(t.box), "chars": t.chars.map { $0.map(r) as Any? ?? NSNull() }] as [String: Any]
        },
        "skipped": Dictionary(uniqueKeysWithValues: result.skipped.map { (String($0.key), $0.value) }),
        "emf_plus_only": result.emfPlusOnly,
    ]
}
