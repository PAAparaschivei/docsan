import CoreImage
import ImageIO
import UniformTypeIdentifiers
import Foundation
let filter = CIFilter(name: "CIQRCodeGenerator")!
filter.setValue(Data("fictional-account-9876543210".utf8), forKey: "inputMessage")
filter.setValue("M", forKey: "inputCorrectionLevel")
let image = filter.outputImage!.transformed(by: CGAffineTransform(scaleX: 8, y: 8))
let context = CIContext()
let rendered = context.createCGImage(image, from: image.extent)!
let target = CGImageDestinationCreateWithURL(URL(fileURLWithPath: CommandLine.arguments[1]) as CFURL, UTType.png.identifier as CFString, 1, nil)!
let opaque = CGContext(data: nil, width: rendered.width, height: rendered.height, bitsPerComponent: 8,
                       bytesPerRow: rendered.width * 4, space: CGColorSpaceCreateDeviceRGB(),
                       bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)!
opaque.setFillColor(CGColor(gray: 1, alpha: 1))
opaque.fill(CGRect(x: 0, y: 0, width: rendered.width, height: rendered.height))
opaque.draw(rendered, in: CGRect(x: 0, y: 0, width: rendered.width, height: rendered.height))
CGImageDestinationAddImage(target, opaque.makeImage()!, nil)
guard CGImageDestinationFinalize(target) else { fatalError("cannot write fixture code") }
