import SwiftUI

/// Spacing scale shared by every screen. Ad hoc values make a layout feel assembled.
enum Space {
    static let xxs: CGFloat = 2
    static let xs: CGFloat = 4
    static let s: CGFloat = 8
    static let m: CGFloat = 12
    static let l: CGFloat = 16
    static let xl: CGFloat = 24
    static let xxl: CGFloat = 32
    static let xxxl: CGFloat = 48
}

/// Type ramp. Hierarchy comes from size and weight; colour is reserved for file states.
enum Typeface {
    static let screenTitle = Font.system(size: 28, weight: .bold)
    static let sectionTitle = Font.system(size: 20, weight: .bold)
    static let bigNumber = Font.system(size: 40, weight: .bold)
    static let rowTitle = Font.system(size: 13, weight: .semibold)
    static let body = Font.system(size: 13)
    static let meta = Font.system(size: 12)
    static let label = Font.system(size: 12, weight: .semibold)
}

/// The three outcomes a file can have. Each carries a symbol and a word, never colour alone.
enum FileState: Hashable {
    case ready, review, blocked

    var symbol: String {
        switch self {
        case .ready: "checkmark.circle.fill"
        case .review: "exclamationmark.circle.fill"
        case .blocked: "minus.circle.fill"
        }
    }

    var tint: Color {
        switch self {
        case .ready: .green
        case .review: .orange
        case .blocked: .red
        }
    }
}

/// Soft fill used for cards and tiles; adapts to light and dark.
extension ShapeStyle where Self == Color {
    static var cardFill: Color { Color.primary.opacity(0.045) }
    static var hairline: Color { Color.primary.opacity(0.09) }
}

struct PrimaryButtonStyle: ButtonStyle {
    var large = false
    var fullWidth = false
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: large ? 14 : 13, weight: .medium))
            .padding(.horizontal, large ? Space.xl : Space.l)
            .frame(maxWidth: fullWidth ? .infinity : nil)
            .frame(height: large ? 38 : 30)
            .foregroundStyle(isEnabled ? Color(nsColor: .windowBackgroundColor) : Color.secondary)
            .background(
                isEnabled ? Color.primary : Color.primary.opacity(0.07),
                in: RoundedRectangle(cornerRadius: large ? 10 : 8, style: .continuous)
            )
            .opacity(configuration.isPressed ? 0.78 : 1)
            .contentShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}

struct SecondaryButtonStyle: ButtonStyle {
    var large = false
    var fullWidth = false
    var height: CGFloat? = nil

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: large ? 14 : 13, weight: .medium))
            .padding(.horizontal, large ? Space.xl : Space.m)
            .frame(maxWidth: fullWidth ? .infinity : nil)
            .frame(height: height ?? (large ? 38 : 30))
            .foregroundStyle(.primary)
            .background(
                Color.primary.opacity(configuration.isPressed ? 0.12 : 0.07),
                in: RoundedRectangle(cornerRadius: large ? 10 : 8, style: .continuous)
            )
            .contentShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
            .animation(.easeOut(duration: 0.12), value: configuration.isPressed)
    }
}
