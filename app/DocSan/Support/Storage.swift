import CryptoKit
import Foundation

enum StorageError: LocalizedError {
    case differentVolume
    case changed(String)

    var errorDescription: String? {
        switch self {
        case .differentVolume:
            "Choose a folder on the same disk as your home folder. Files can only be placed there in one atomic step."
        case .changed(let name):
            "\(name) changed after verification, so it was not exported."
        }
    }
}

/// The app's private storage inside its sandbox container. Originals, profiles,
/// staging and reports never leave it; only verified files are published.
struct Storage {
    let root: URL

    /// `name` lets the self-test use its own folder, never the user's data.
    static func standard(named name: String = "DocSan") throws -> Storage {
        let support = try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask,
                                                  appropriateFor: nil, create: true)
        let storage = Storage(root: support.appendingPathComponent(name, isDirectory: true))
        try storage.prepare()
        return storage
    }

    var profile: URL { root.appendingPathComponent("profiles/active.json") }
    var lexicon: URL { root.appendingPathComponent("lexicon", isDirectory: true) }
    var spellcheckerWords: URL { lexicon.appendingPathComponent("macos-spellchecker.txt") }
    var privateDir: URL { root.appendingPathComponent("private", isDirectory: true) }

    func prepare() throws {
        let fm = FileManager.default
        for dir in [root, profile.deletingLastPathComponent(), lexicon, privateDir,
                    root.appendingPathComponent("inbox"), root.appendingPathComponent("outbox")] {
            try fm.createDirectory(at: dir, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        }
        if !fm.fileExists(atPath: spellcheckerWords.path) {
            try Data("# Words the macOS spell checker recognises in English or French.\n".utf8)
                .write(to: spellcheckerWords)
        }
    }

    func newFolder(_ kind: String) throws -> URL {
        let url = root.appendingPathComponent(kind).appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        return url
    }

    /// Copies dropped files and folder contents into private staging.
    func importFiles(_ urls: [URL], into inbox: URL) throws -> [URL] {
        let fm = FileManager.default
        var copied: [URL] = []
        func copy(_ url: URL) throws {
            let values = try url.resourceValues(forKeys: [.isDirectoryKey, .isRegularFileKey, .isSymbolicLinkKey])
            if values.isSymbolicLink == true || url.lastPathComponent.hasPrefix(".") { return }
            if values.isDirectory == true {
                let children = try fm.contentsOfDirectory(at: url, includingPropertiesForKeys: nil)
                for child in children.sorted(by: { $0.lastPathComponent < $1.lastPathComponent }) { try copy(child) }
            } else if values.isRegularFile == true {
                var target = inbox.appendingPathComponent(url.lastPathComponent)
                var n = 2
                while fm.fileExists(atPath: target.path) {
                    target = inbox.appendingPathComponent("\(url.deletingPathExtension().lastPathComponent) \(n).\(url.pathExtension)")
                    n += 1
                }
                try fm.copyItem(at: url, to: target)
                copied.append(target)
            }
        }
        for url in urls {
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            try copy(url)
        }
        return copied
    }

    func importProfile(from url: URL) throws {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        let data = try Data(contentsOf: url)
        try data.write(to: profile, options: [.atomic])
    }

    /// Records dictionary words so the engine treats them as familiar.
    func addKnownWords(_ words: Set<String>) throws {
        guard !words.isEmpty else { return }
        let handle = try FileHandle(forWritingTo: spellcheckerWords)
        defer { try? handle.close() }
        try handle.seekToEnd()
        try handle.write(contentsOf: Data((words.sorted().joined(separator: "\n") + "\n").utf8))
    }

    /// Places verified files in a new subfolder of the chosen folder. Each file
    /// is hard-linked in one atomic step after its hash is checked again.
    func publish(files: [(url: URL, sha256: String)], summary: URL?, to folder: URL, named: String) throws -> URL {
        let fm = FileManager.default
        let scoped = folder.startAccessingSecurityScopedResource()
        defer { if scoped { folder.stopAccessingSecurityScopedResource() } }
        guard try sameVolume(files.first?.url ?? root, folder) else { throw StorageError.differentVolume }
        var target = folder.appendingPathComponent(named, isDirectory: true)
        var n = 2
        while fm.fileExists(atPath: target.path) {
            target = folder.appendingPathComponent("\(named) \(n)", isDirectory: true)
            n += 1
        }
        try fm.createDirectory(at: target, withIntermediateDirectories: false)
        for file in files {
            guard try sha256(file.url) == file.sha256 else { throw StorageError.changed(file.url.lastPathComponent) }
            let destination = target.appendingPathComponent(file.url.lastPathComponent)
            try fm.linkItem(at: file.url, to: destination)
            guard try sha256(destination) == file.sha256 else {
                try? fm.removeItem(at: destination)
                throw StorageError.changed(file.url.lastPathComponent)
            }
        }
        if let summary {
            try fm.linkItem(at: summary, to: target.appendingPathComponent("summary.json"))
        }
        return target
    }

    private func sameVolume(_ a: URL, _ b: URL) throws -> Bool {
        let va = try a.resourceValues(forKeys: [.volumeIdentifierKey]).volumeIdentifier as? NSObject
        let vb = try b.resourceValues(forKeys: [.volumeIdentifierKey]).volumeIdentifier as? NSObject
        return va != nil && va == vb
    }

    private func sha256(_ url: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        var hasher = SHA256()
        while let chunk = try handle.read(upToCount: 1 << 20), !chunk.isEmpty {
            hasher.update(data: chunk)
        }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }
}
