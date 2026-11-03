import SwiftUI

/// A rounded square holding an SF Symbol, used for file types and section markers.
struct IconTile: View {
    let symbol: String
    var size: CGFloat = 32
    var tint: Color = .primary

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: size * 0.26, style: .continuous)
        Image(systemName: symbol)
            .font(.system(size: size * 0.48, weight: .regular))
            .symbolRenderingMode(.hierarchical)
            .foregroundStyle(tint)
            .frame(width: size, height: size)
            .background(.cardFill, in: shape)
            .overlay(shape.strokeBorder(.hairline, lineWidth: 0.5))
            .accessibilityHidden(true)
    }
}

/// A file state as a small symbol plus a word.
struct StateLabel: View {
    let state: FileState
    let text: String

    var body: some View {
        HStack(spacing: Space.xs) {
            Image(systemName: state.symbol)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(state.tint)
            Text(text)
                .font(Typeface.meta)
                .foregroundStyle(.secondary)
        }
        .accessibilityElement(children: .combine)
    }
}

/// A keyboard key shown next to the action it triggers.
struct KeyCap: View {
    let key: String

    var body: some View {
        Text(key)
            .font(.system(size: 11, weight: .semibold, design: .monospaced))
            .foregroundStyle(.secondary)
            .frame(minWidth: 20, minHeight: 20)
            .background(Color(nsColor: .windowBackgroundColor), in: RoundedRectangle(cornerRadius: 5, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 5, style: .continuous).strokeBorder(Color.primary.opacity(0.18), lineWidth: 0.75))
            .accessibilityHidden(true)
    }
}

/// A replacement label such as [CLIENT], in monospaced type.
struct LabelTag: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.system(size: 11, weight: .semibold, design: .monospaced))
            .padding(.horizontal, 5)
            .padding(.vertical, 1)
            .background(Color.primary.opacity(0.08), in: RoundedRectangle(cornerRadius: 4, style: .continuous))
    }
}

/// Grouped container with soft fill; rows inside are separated by inset hairlines.
struct Card<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        VStack(spacing: 0) { content }
            .background(.cardFill, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

struct InsetDivider: View {
    var leading: CGFloat = 56

    var body: some View {
        Rectangle()
            .fill(.hairline)
            .frame(height: 1)
            .padding(.leading, leading)
            .padding(.trailing, Space.l)
    }
}

/// One file in a grouped list: tile, name, detail and a trailing accessory.
struct FileRow<Trailing: View>: View {
    let symbol: String
    let title: String
    let detail: String
    @ViewBuilder var trailing: Trailing

    var body: some View {
        HStack(spacing: Space.m) {
            IconTile(symbol: symbol)
            VStack(alignment: .leading, spacing: Space.xxs) {
                Text(title).font(Typeface.rowTitle)
                Text(detail).font(Typeface.meta).foregroundStyle(.secondary)
            }
            Spacer(minLength: Space.m)
            trailing
        }
        .padding(.vertical, 10)
        .padding(.leading, Space.m)
        .padding(.trailing, Space.l)
    }
}

/// Large number with a unit below it, right-aligned, like a gauge readout.
struct Readout: View {
    let value: Int
    let unit: String

    var body: some View {
        VStack(alignment: .trailing, spacing: Space.xxs) {
            Text("\(value)")
                .font(Typeface.bigNumber)
                .monospacedDigit()
                .contentTransition(.numericText(value: Double(value)))
            Text(unit).font(Typeface.body).foregroundStyle(.secondary)
        }
        .accessibilityElement(children: .combine)
    }
}

struct SectionLabel: View {
    let text: String

    var body: some View {
        Text(text)
            .font(Typeface.label)
            .foregroundStyle(.secondary)
    }
}
