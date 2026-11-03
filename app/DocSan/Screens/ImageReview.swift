import ImageIO
import SwiftUI

/// The image as the engine reads it, upright, with its masks. Original shows
/// what will be masked; Sanitized shows the masks burned in, as exported.
/// Dragging across the image adds a mask; hovering over one of the owner's
/// masks offers to remove it.
struct ImageCanvas: View {
    @Bindable var model: AppModel
    let picture: Picture
    let mode: PreviewMode
    var search: String = ""
    var currentLine: Int? = nil
    @State private var pixels: CGImage?
    @State private var dragStart: CGPoint?
    @State private var dragNow: CGPoint?
    @State private var hovered: CGRect?

    var body: some View {
        Group {
            if let pixels {
                let image = picture.image
                Image(decorative: pixels, scale: 1)
                    .resizable()
                    .interpolation(.high)
                    .aspectRatio(contentMode: .fit)
                    .frame(maxWidth: min(CGFloat(image.width), 960))
                    .overlay {
                        GeometryReader { geo in
                            overlay(image, scale: geo.size.width / CGFloat(max(image.width, 1)))
                        }
                    }
                    .clipShape(RoundedRectangle(cornerRadius: 3))
                    .shadow(color: .black.opacity(0.07), radius: 0.5)
                    .shadow(color: .black.opacity(0.10), radius: 16, y: 6)
            } else {
                ProgressView().controlSize(.small).frame(height: 240)
            }
        }
        .task(id: picture.source) { pixels = Self.load(picture.source) }
    }

    private var dragRect: CGRect? {
        guard let a = dragStart, let b = dragNow else { return nil }
        return CGRect(x: min(a.x, b.x), y: min(a.y, b.y), width: abs(a.x - b.x), height: abs(a.y - b.y))
    }

    private func overlay(_ image: EngineImage, scale s: CGFloat) -> some View {
        ZStack(alignment: .topLeading) {
            Color.clear.contentShape(Rectangle())
            if picture.isRemoved {
                removedCover(image, scale: s)
            } else {
                ForEach(Array(image.masks.enumerated()), id: \.offset) { _, mask in
                    maskView(mask, scale: s)
                }
            }
            ForEach(Array(searchRects(image).enumerated()), id: \.offset) { _, hit in
                let r = hit.rect
                RoundedRectangle(cornerRadius: 2)
                    .strokeBorder(hit.current ? Color.orange : Color.accentColor, lineWidth: hit.current ? 3 : 2)
                    .background((hit.current ? Color.orange : Color.accentColor).opacity(0.14))
                    .frame(width: r.width * s, height: r.height * s)
                    .offset(x: r.minX * s, y: r.minY * s)
                    .allowsHitTesting(false)
            }
            ForEach(Array(image.foundRects.enumerated()), id: \.offset) { _, r in
                RoundedRectangle(cornerRadius: 2)
                    .strokeBorder(Color.orange, style: StrokeStyle(lineWidth: 2, dash: [5, 3]))
                    .frame(width: r.width * s, height: r.height * s)
                    .offset(x: r.minX * s, y: r.minY * s)
                    .allowsHitTesting(false)
            }
            if let r = dragRect {
                Rectangle()
                    .fill(Color.black.opacity(mode == .sanitized ? 0.85 : 0.2))
                    .overlay(Rectangle().strokeBorder(Color.accentColor, style: StrokeStyle(lineWidth: 1.5, dash: [4, 3])))
                    .frame(width: r.width, height: r.height)
                    .offset(x: r.minX, y: r.minY)
                    .allowsHitTesting(false)
            }
        }
        .gesture(
            DragGesture(minimumDistance: 3)
                .onChanged { value in
                    dragStart = value.startLocation
                    dragNow = value.location
                }
                .onEnded { _ in
                    guard let r = dragRect, model.busy == nil, !picture.isRemoved else { dragStart = nil; dragNow = nil; return }
                    dragStart = nil
                    dragNow = nil
                    let pixels = CGRect(x: r.minX / s, y: r.minY / s, width: r.width / s, height: r.height / s)
                    Task { await model.addMask(pixels, to: picture) }
                }
        )
        .help("Drag to cover anything that identifies the company")
    }

    /// A picture removed whole: in Sanitized, nothing of it is left.
    @ViewBuilder private func removedCover(_ image: EngineImage, scale s: CGFloat) -> some View {
        if mode == .sanitized {
            Rectangle()
                .fill(Color(white: 0.94))
                .overlay {
                    Label("Removed from the output", systemImage: "minus.circle")
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(Color(white: 0.35))
                }
                .frame(width: CGFloat(image.width) * s, height: CGFloat(image.height) * s)
                .allowsHitTesting(false)
        }
    }

    private func maskView(_ mask: EngineMask, scale s: CGFloat) -> some View {
        let r = mask.rect
        return ZStack(alignment: .topLeading) {
            if mode == .sanitized {
                Rectangle().fill(.black)
            } else {
                Rectangle()
                    .fill(Color.black.opacity(0.12))
                    .overlay(Rectangle().strokeBorder(Color.black.opacity(0.7), lineWidth: 1.5))
                if r.height * s >= 14 {
                    Text(mask.isOwner ? "YOURS" : mask.label)
                        .font(.system(size: 9, weight: .bold, design: .monospaced))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 3)
                        .padding(.vertical, 1)
                        .background(Color.black.opacity(0.75))
                }
            }
            if mask.isOwner && hovered == r {
                Button {
                    Task { await model.removeMask(r, from: picture) }
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 15))
                        .symbolRenderingMode(.palette)
                        .foregroundStyle(.white, Color.red)
                }
                .buttonStyle(.plain)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topTrailing)
                .offset(x: 7, y: -7)
                .help("Remove this mask")
            }
        }
        .frame(width: max(r.width * s, 2), height: max(r.height * s, 2))
        .offset(x: r.minX * s, y: r.minY * s)
        .onHover { inside in
            guard mask.isOwner else { return }
            if inside { hovered = r } else if hovered == r { hovered = nil }
        }
    }

    /// Lines that contain the search text: in Original, everywhere it was read;
    /// in Sanitized, only where no mask covers it.
    private func searchRects(_ image: EngineImage) -> [(rect: CGRect, current: Bool)] {
        let needle = search.trimmingCharacters(in: .whitespaces)
        guard needle.count >= 2 else { return [] }
        return image.lines.indices.compactMap { i in
            let text = mode == .original ? image.lines[i].text : (i < image.visible.count ? image.visible[i] : "")
            return AppModel.count(needle, in: [text]) > 0 ? (pixelRect(image.lines[i].box), i == currentLine) : nil
        }
    }

    /// The upright image, the way the engine and the helper read it.
    static func load(_ path: String) -> CGImage? {
        guard let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil),
              let props = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let w = props[kCGImagePropertyPixelWidth] as? Int,
              let h = props[kCGImagePropertyPixelHeight] as? Int
        else { return nil }
        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceThumbnailMaxPixelSize: max(w, h),
        ]
        return CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary)
    }
}

/// The decision panel for an image: what was masked, what the second check
/// still reads, and the confirmation the engine needs before release.
struct ImagePanel: View {
    @Bindable var model: AppModel
    let item: BatchItem
    let picture: Picture

    private var image: EngineImage { picture.image }
    private var found: [CGRect] { image.foundRects }
    private var textMasks: Int { image.masks.filter { !$0.isOwner }.count }

    var body: some View {
        VStack(alignment: .leading, spacing: Space.l) {
            VStack(alignment: .leading, spacing: Space.xs) {
                if picture.index != nil {
                    Text("\(picture.title) of \(item.pictures.count) · \(item.name)")
                        .font(Typeface.meta)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                Text(title).font(Typeface.sectionTitle).kerning(-0.3)
                Text(message)
                    .font(Typeface.body)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if picture.isRemoved {
                if picture.isWordPicture {
                    Button("Keep It, with Names Masked") { Task { await model.setRemoved(false, picture) } }
                        .buttonStyle(SecondaryButtonStyle())
                        .disabled(model.busy != nil)
                }
            } else {
                masksSection
            }
        }
    }

    @ViewBuilder private var masksSection: some View {
        VStack(alignment: .leading, spacing: Space.l) {
            if image.logo == true, picture.isWordPicture {
                Card {
                    HStack(alignment: .top, spacing: Space.m) {
                        IconTile(symbol: "seal", tint: .orange)
                        VStack(alignment: .leading, spacing: Space.s) {
                            Text("Looks like a logo").font(Typeface.rowTitle)
                            Text("DocSan read a company reference here and little else. Masking the name leaves the rest of the mark, which still identifies the company.")
                                .font(Typeface.meta)
                                .foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                            Button("Remove Picture") { removeWhole() }
                                .buttonStyle(SecondaryButtonStyle())
                                .disabled(model.busy != nil)
                        }
                    }
                    .padding(Space.m)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            if !found.isEmpty {
                Card {
                    HStack(alignment: .top, spacing: Space.m) {
                        IconTile(symbol: "text.viewfinder", tint: .orange)
                        VStack(alignment: .leading, spacing: Space.s) {
                            Text("Text still readable").font(Typeface.rowTitle)
                            Text("The second check read \(found.count == 1 ? "one place" : "\(found.count) places") that may identify the company, outlined in orange.")
                                .font(Typeface.meta)
                                .foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                            Button("Mask These Areas") { Task { await model.maskFound(in: picture) } }
                                .buttonStyle(SecondaryButtonStyle())
                                .disabled(model.busy != nil)
                        }
                    }
                    .padding(Space.m)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            VStack(alignment: .leading, spacing: Space.s) {
                SectionLabel(text: "Masks").padding(.leading, Space.m)
                if groups.isEmpty {
                    Text(picture.isPage ? "No company reference was read on this page." : "No company reference was read in this image.")
                        .font(Typeface.body)
                        .foregroundStyle(.secondary)
                        .padding(.leading, Space.m)
                } else {
                    Card {
                        ForEach(Array(groups.enumerated()), id: \.element.label) { i, group in
                            if i > 0 { InsetDivider(leading: Space.m) }
                            HStack {
                                if group.label == "MASK" {
                                    Text("Drawn by you").font(Typeface.body)
                                } else {
                                    LabelTag(text: group.label)
                                }
                                Spacer()
                                Text("\(group.count)").font(Typeface.body).monospacedDigit().foregroundStyle(.secondary)
                            }
                            .padding(.horizontal, Space.m)
                            .padding(.vertical, 9)
                        }
                    }
                }
            }
            AreaMaskEditor(model: model, picture: picture).id(picture.key)
            HStack(alignment: .top, spacing: Space.s) {
                Image(systemName: "rectangle.dashed").font(.system(size: 12, weight: .medium))
                Text(picture.isWordPicture
                     ? "Drag across the image to cover anything else that identifies the company. Hover over a mask you drew to remove it. A logo is better removed whole."
                     : "Drag across the \(picture.isPage ? "page" : "image") to cover a logo or anything else that identifies the company. Hover over a mask you drew to remove it.")
                    .fixedSize(horizontal: false, vertical: true)
            }
            .font(Typeface.meta)
            .foregroundStyle(.secondary)
            HStack(spacing: Space.s) {
                if image.confirmed {
                    Button("Change Masks") { Task { await model.withdrawConfirmation(picture) } }
                        .buttonStyle(SecondaryButtonStyle())
                        .disabled(model.busy != nil)
                }
                if picture.isWordPicture, image.logo != true {
                    Button("Remove Picture") { removeWhole() }
                        .buttonStyle(SecondaryButtonStyle())
                        .disabled(model.busy != nil)
                }
            }
        }
    }

    /// Removes the picture whole, then shows the next one still waiting.
    private func removeWhole() {
        Task {
            await model.setRemoved(true, picture)
            if let next = model.awaitingPictures.first { model.show(next.picture, in: next.item) }
        }
    }

    private var groups: [(label: String, count: Int)] {
        let labels = image.masks.map(\.label)
        return Array(Set(labels)).sorted { a, b in (a == "MASK" ? 1 : 0, a) < (b == "MASK" ? 1 : 0, b) }
            .map { label in (label, labels.filter { $0 == label }.count) }
    }

    private var title: String {
        let thing = picture.isPage ? "page" : "image"
        if picture.isRemoved { return "Removed whole" }
        if image.confirmed { return item.state == .blocked && !found.isEmpty ? "Check the \(thing)" : "Masks confirmed" }
        if item.state == .blocked { return found.isEmpty ? (picture.isPage ? "Page blocked" : "Image blocked") : "Check the \(thing)" }
        return "Confirm the masks"
    }

    private var message: String {
        switch image.removed {
        case "margin":
            return "This picture is in a header or footer, where documents carry their logos, so nothing of it goes into the output."
        case "same":
            return "This picture matches another picture removed from this document, so it is left out too."
        case "profile":
            return "This picture matches a logo removed from an earlier document, so nothing of it goes into the output."
        case .some:
            return "You removed this picture. Nothing of it goes into the output."
        case nil:
            break
        }
        if !found.isEmpty {
            return "The masks were applied, but the second check still reads text that may identify the company."
        }
        if image.confirmed {
            return "The masks are burned into the pixels on export. Changing a mask asks you to confirm again."
        }
        if item.state == .blocked { return item.file.reasons.first ?? "This image could not be checked." }
        let masked = "DocSan masked \(textMasks == 1 ? "one area" : "\(textMasks) areas") using the profile and redaction rules."
        if picture.isPage {
            let source = image.textLayer == false
                ? " This page has no usable text layer, so its text comes from OCR."
                : " Its text comes from the page's own text layer, and pictures on it are read by OCR."
            return masked + source + " Text DocSan cannot read, such as a logo, is up to you: look over the whole page before you confirm."
        }
        if picture.index != nil {
            return masked + " Text it cannot read is up to you: look over the whole image before you confirm, and remove the picture if it is a logo."
        }
        return masked + " Text it cannot read, such as a logo, is up to you: look over the whole image before you confirm."
    }
}

/// An accessible alternative to pointer drawing, relative to the page shown.
private struct AreaMaskEditor: View {
    @Bindable var model: AppModel
    let picture: Picture
    @State private var left = "5"
    @State private var top = "5"
    @State private var width = "10"
    @State private var height = "10"

    private var rect: CGRect? {
        guard let x = Double(left), let y = Double(top), let w = Double(width), let h = Double(height),
              [x,y,w,h].allSatisfy({ $0.isFinite }), x >= 0, y >= 0, w > 0, h > 0,
              x + w <= 100, y + h <= 100 else { return nil }
        return CGRect(x: x / 100 * Double(picture.image.width), y: y / 100 * Double(picture.image.height),
                      width: w / 100 * Double(picture.image.width), height: h / 100 * Double(picture.image.height))
    }

    var body: some View {
        DisclosureGroup("Add an area mask") {
            VStack(alignment: .leading, spacing: Space.s) {
                Text("Position and size as percentages of the displayed page. The top-left corner is 0, 0.")
                    .font(Typeface.meta).foregroundStyle(.secondary)
                HStack {
                    field("Left (%)", $left); field("Top (%)", $top)
                }
                HStack {
                    field("Width (%)", $width); field("Height (%)", $height)
                }
                Button("Cover This Area") {
                    guard let rect else { return }
                    Task { await model.addMask(rect, to: picture) }
                }
                .buttonStyle(SecondaryButtonStyle())
                .disabled(rect == nil || model.busy != nil || picture.isRemoved)
                ForEach(Array(picture.image.masks.filter(\.isOwner).enumerated()), id: \.offset) { n, mask in
                    Button("Remove area mask \(n + 1)") { Task { await model.removeMask(mask.rect, from: picture) } }
                        .disabled(model.busy != nil)
                }
            }.padding(.top, Space.s)
        }
    }

    private func field(_ title: String, _ value: Binding<String>) -> some View {
        VStack(alignment: .leading) {
            Text(title).font(Typeface.meta)
            TextField(title, text: value).textFieldStyle(.roundedBorder).accessibilityLabel(title)
        }
    }
}
