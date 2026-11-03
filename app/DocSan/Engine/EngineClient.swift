import Foundation

/// Results returned by the engine bridge (`docsan.bridge`).
struct EngineLine: Decodable, Hashable {
    let kind: String
    let level: Int
    let text: String
}

struct EngineReview: Decodable, Hashable {
    let word: String
    let context: String
    let count: Int
    let kind: String?  // "place" for countries, nationalities and cities
}

struct EngineFile: Decodable, Hashable, Identifiable {
    let source: String
    var status: String
    var reasons: [String]
    let review: [EngineReview]
    let output: String?
    let sha256: String?
    let replacements: [String: Int]
    let previewOriginal: [EngineLine]
    let previewSanitized: [EngineLine]
    let kind: String
    var image: EngineImage?
    var images: [EngineImage]  // pictures inside a Word file, or the pages of a PDF
    let pages: String?  // the sanitized file's pages as HTML, masks as proposed; private, never exported
    let pagesOriginal: String?  // the original's pages, drawn the same way

    var id: String { source }
}

/// A box in pixels of the upright image, origin top left.
func pixelRect(_ box: [Double]) -> CGRect {
    box.count == 4 ? CGRect(x: box[0], y: box[1], width: box[2], height: box[3]) : .zero
}

struct EngineMask: Decodable, Hashable {
    let box: [Double]
    let label: String
    let source: String  // "text" (found by recognition) or "owner"

    var rect: CGRect { pixelRect(box) }
    var isOwner: Bool { source == "owner" }
}

struct EngineImageLine: Decodable, Hashable {
    let box: [Double]
    let text: String
}

struct EngineImage: Decodable, Hashable {
    let width: Int
    let height: Int
    let format: String
    let masks: [EngineMask]
    let lines: [EngineImageLine]  // what recognition read, before masking
    let visible: [String]  // each line's text that no mask covers
    let digest: String
    var confirmed: Bool
    let found: [[Double]]  // where the verifier's OCR read a reference
    let logo: Bool?  // a company reference with little else around it: probably a logo
    // Pictures inside a Word file only:
    let key: String?
    let index: Int?
    let source: String?  // a private copy of the picture, for display
    let removed: String?  // why it is removed whole: "chosen", "margin", "profile" or "same"
    // Pages of a PDF only:
    let textLayer: Bool?  // the page's own text layer is used; false: its text comes from OCR

    var foundRects: [CGRect] { found.map(pixelRect) }
}

struct EngineProfile: Decodable, Hashable {
    let name: String
    let policy: String
    let terms: Int
    let termsByLabel: [String: Int]
    let redactSystemNames: Bool
    let personStyle: String  // "first_name_initial" or "label"
}

struct ProfileWords: Decodable, Hashable {
    struct Term: Decodable, Hashable {
        let text: String
        let label: String
    }
    struct Kept: Decodable, Hashable {
        let word: String
        let kind: String  // "place", "glossary" or ""
    }
    let safe: [Kept]
    let terms: [Term]
}

struct ProfileWordsResponse: Decodable {
    let words: ProfileWords
}

struct BatchResponse: Decodable {
    let batch: String
    let files: [EngineFile]
    let summary: String?
}

struct ProfileResponse: Decodable {
    let profile: EngineProfile
}

enum EngineError: LocalizedError {
    case notBundled
    case failed(String)

    var errorDescription: String? {
        switch self {
        case .notBundled: "The processing engine is not included in this build of DocSan."
        case .failed(let message): message
        }
    }
}

/// Runs the bundled engine as a helper process. The helper inherits the app's
/// sandbox, so it can only reach the app's container and cannot use the network.
struct EngineClient: Sendable {
    let python: URL
    let launcher: URL

    static func bundled() -> EngineClient? {
        guard let resources = Bundle.main.resourceURL else { return nil }
        let root = resources.appendingPathComponent("engine", isDirectory: true)
        let python = root.appendingPathComponent("python/bin/python3")
        let launcher = root.appendingPathComponent("launch.py")
        let fm = FileManager.default
        guard fm.isExecutableFile(atPath: python.path), fm.fileExists(atPath: launcher.path) else { return nil }
        return EngineClient(python: python, launcher: launcher)
    }

    func call<T: Decodable & Sendable>(_ request: [String: Any], as type: T.Type) async throws -> T {
        let body = try JSONSerialization.data(withJSONObject: request)
        let python = python, launcher = launcher
        let data = try await Task.detached(priority: .userInitiated) {
            try Self.run(python: python, launcher: launcher, input: body)
        }.value
        let envelope = try JSONDecoder().decode(Envelope.self, from: data)
        guard envelope.ok else { throw EngineError.failed(envelope.error ?? "The engine reported an unknown error.") }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(T.self, from: data)
    }

    private struct Envelope: Decodable {
        let ok: Bool
        let error: String?
    }

    private static func run(python: URL, launcher: URL, input: Data) throws -> Data {
        let process = Process()
        process.executableURL = python
        // -I isolates the interpreter from the environment; -B never writes
        // bytecode into the signed app bundle.
        process.arguments = ["-I", "-B", launcher.path]
        // CFFIXED_USER_HOME tells system frameworks in the helpers where the
        // container is; without it Vision looks for its model cache in the real
        // home folder, which the sandbox refuses.
        var environment = [
            "PATH": "/usr/bin:/bin",
            "LANG": "en_US.UTF-8",
            "HOME": NSHomeDirectory(),
            "CFFIXED_USER_HOME": NSHomeDirectory(),
            "TMPDIR": NSTemporaryDirectory(),
        ]
        if let container = ProcessInfo.processInfo.environment["APP_SANDBOX_CONTAINER_ID"] {
            environment["APP_SANDBOX_CONTAINER_ID"] = container
        }
        process.environment = environment
        let stdin = Pipe(), stdout = Pipe()
        let errLog = FileManager.default.temporaryDirectory.appendingPathComponent("engine-stderr.log")
        FileManager.default.createFile(atPath: errLog.path, contents: nil)
        let stderr = try FileHandle(forWritingTo: errLog)
        defer { try? stderr.close() }
        process.standardInput = stdin
        process.standardOutput = stdout
        process.standardError = stderr
        try process.run()
        stdin.fileHandleForWriting.write(input)
        try stdin.fileHandleForWriting.close()
        let output = stdout.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        if output.isEmpty {
            let tail = (try? String(contentsOf: errLog, encoding: .utf8))?.suffix(600) ?? ""
            throw EngineError.failed("The engine stopped without an answer (status \(process.terminationStatus)). \(tail)")
        }
        return output
    }
}
