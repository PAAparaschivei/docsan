import Foundation
import CoreGraphics
import Vision

/// Geometric observations only. The engine decides which regions to mask.
/// No decoded barcode payload is returned or logged.
func sensitiveRegions(_ canvas: Canvas) throws -> [String: Any] {
    var codes: [[Double]] = []
    func scan(_ region: Canvas, x: Double = 0, y: Double = 0) throws {
        let request = VNDetectBarcodesRequest()
        try VNImageRequestHandler(cgImage: region.image, options: [:]).perform([request])
        for observation in request.results ?? [] {
            let b = observation.boundingBox
            let box = CGRect(x: x + b.minX * Double(region.width), y: y + (1-b.maxY) * Double(region.height),
                             width: b.width * Double(region.width), height: b.height * Double(region.height))
            let duplicate = codes.contains { other in
                let old = CGRect(x: other[0], y: other[1], width: other[2], height: other[3])
                let common = old.intersection(box)
                return !common.isNull && common.width * common.height > 0.5 * min(old.width*old.height, box.width*box.height)
            }
            if !duplicate { codes.append([box.minX, box.minY, box.width, box.height]) }
        }
    }
    try scan(canvas)
    for rect in tileRects(width: canvas.width, height: canvas.height, size: 1536, overlap: 384) {
        try scan(piece(canvas, rect), x: rect.minX, y: rect.minY)
    }
    // Connected saturated ink at half resolution. A coloured diagram is also
    // reported; only a small component beside an isolated company mark in a
    // page margin qualifies as branding in the engine.
    guard let base = canvas.context.data else { throw Failure(message: "cannot inspect page regions") }
    let px = base.assumingMemoryBound(to: UInt8.self)
    let sample = 2, w = (canvas.width + 1) / 2, h = (canvas.height + 1) / 2
    var bits = [UInt8](repeating: 0, count: w * h)
    for y in 0..<h { for x in 0..<w {
        let i = min(canvas.height - 1, y * sample) * canvas.context.bytesPerRow + min(canvas.width - 1, x * sample) * 4
        let channels = [Int(px[i]), Int(px[i + 1]), Int(px[i + 2])]
        let hi = channels.max()!, lo = channels.min()!
        if hi - lo > 55 && hi > 100 && lo < 220 { bits[y * w + x] = 1 }
    } }
    var components: [[Double]] = []
    for seed in bits.indices where bits[seed] == 1 {
        var queue = [seed], head = 0
        bits[seed] = 0
        var x0 = seed % w, x1 = x0, y0 = seed / w, y1 = y0
        while head < queue.count {
            let p = queue[head]; head += 1
            let x = p % w, y = p / w
            x0 = min(x0, x); x1 = max(x1, x); y0 = min(y0, y); y1 = max(y1, y)
            for (nx, ny) in [(x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)] {
                if nx >= 0 && nx < w && ny >= 0 && ny < h {
                    let n = ny * w + nx
                    if bits[n] == 1 { bits[n] = 0; queue.append(n) }
                }
            }
        }
        if queue.count >= 16 && x1 - x0 >= 3 && y1 - y0 >= 3 {
            components.append([Double(x0 * sample), Double(y0 * sample),
                               Double((x1 - x0 + 1) * sample), Double((y1 - y0 + 1) * sample)])
        }
    }
    return ["barcodes": codes, "code_candidates": matrixRegions(canvas, threshold: 160) + matrixRegions(canvas, threshold: 215), "colour_components": components]
}


/// Isolated, dense square ink with transitions in both directions can be a
/// damaged matrix code even when Vision cannot decode it. Return bounds only.
func matrixRegions(_ canvas: Canvas, threshold: Int) -> [[Double]] {
    guard let base = canvas.context.data else { return [] }
    let px = base.assumingMemoryBound(to: UInt8.self)
    let sample = max(1, Int(ceil(Double(max(canvas.width, canvas.height)) / 2400)))
    let w = (canvas.width + sample - 1) / sample, h = (canvas.height + sample - 1) / sample
    var ink = [UInt8](repeating: 0, count: w*h)
    for y in 0..<h { for x in 0..<w {
        let i = min(canvas.height-1, y*sample)*canvas.context.bytesPerRow + min(canvas.width-1, x*sample)*4
        let rgb = [Int(px[i]), Int(px[i+1]), Int(px[i+2])]
        if rgb.max()! - rgb.min()! < 65 && (rgb[0]*3 + rgb[1]*6 + rgb[2])/10 < threshold { ink[y*w+x] = 1 }
    } }
    // One-pixel dilation connects neighboring modules without joining ordinary
    // rows separated by a line space. Test the original pixels, not dilation.
    var joined = [UInt8](repeating: 0, count: w*h)
    for y in 0..<h { for x in 0..<w where ink[y*w+x] == 1 {
        for ny in max(0,y-1)...min(h-1,y+1) { for nx in max(0,x-1)...min(w-1,x+1) { joined[ny*w+nx] = 1 } }
    } }
    var result: [[Double]] = []
    for seed in joined.indices where joined[seed] == 1 {
        joined[seed] = 0
        var queue = [seed], head = 0
        var x0 = seed%w, x1 = x0, y0 = seed/w, y1 = y0
        while head < queue.count {
            let pos = queue[head]; head += 1
            let x = pos%w, y = pos/w
            x0=min(x0,x); x1=max(x1,x); y0=min(y0,y); y1=max(y1,y)
            for (nx,ny) in [(x-1,y),(x+1,y),(x,y-1),(x,y+1)] where nx >= 0 && nx < w && ny >= 0 && ny < h {
                let n=ny*w+nx
                if joined[n] == 1 { joined[n]=0; queue.append(n) }
            }
        }
        let bw=x1-x0+1, bh=y1-y0+1
        guard bw >= 10 && bh >= 10 && bw <= 150 && bh <= 150,
              Double(bw)/Double(bh) > 0.65 && Double(bw)/Double(bh) < 1.5 else { continue }
        var neutral=0
        for y in y0...y1 { for x in x0...x1 {
            let i=min(canvas.height-1,y*sample)*canvas.context.bytesPerRow+min(canvas.width-1,x*sample)*4
            let rgb=[Int(px[i]),Int(px[i+1]),Int(px[i+2])]
            if rgb.max()!-rgb.min()!<12 { neutral += 1 }
        } }
        guard neutral*10>=bw*bh*9 else { continue } // colored artwork is not a grayscale matrix
        var count=0, rows=[Int](), cols=[Int]()
        for y in y0...y1 {
            var changes=0
            for x in x0...x1 {
                count += Int(ink[y*w+x])
                if x>x0 && ink[y*w+x] != ink[y*w+x-1] { changes += 1 }
            }
            rows.append(changes)
        }
        for x in x0...x1 {
            var changes=0
            for y in y0...y1 where y>y0 && ink[y*w+x] != ink[(y-1)*w+x] { changes += 1 }
            cols.append(changes)
        }
        let density=Double(count)/Double(bw*bh)
        guard density > 0.18 && density < 0.78,
              rows.filter({$0>=6}).count*2 >= bh, cols.filter({$0>=6}).count*2 >= bw else { continue }
        var border=0, dark=0
        for y in max(0,y0-2)...min(h-1,y1+2) { for x in max(0,x0-2)...min(w-1,x1+2) {
            if x<x0 || x>x1 || y<y0 || y>y1 { border += 1; dark += Int(ink[y*w+x]) }
        } }
        guard border>0 && dark*5<border else { continue }
        result.append([Double(x0*sample),Double(y0*sample),Double(bw*sample),Double(bh*sample)])
    }
    return result
}
