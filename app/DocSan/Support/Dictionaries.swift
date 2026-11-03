import AppKit

/// Dictionary lookups through the macOS spell checker, for English and French.
/// This is the word source for the unfamiliar-word review (decision D-020).
/// Lookups run on this Mac; words a user has taught the spell checker
/// also count as known.
///
/// Only the lowercase form is checked. The spell checker also accepts proper
/// nouns ("Halden", "Paris"), and those are exactly the names the review must
/// show, so "The" passes as "the" while "Halden" goes to review.
@MainActor
enum Dictionaries {
    static func knownWords(_ words: [String]) -> Set<String> {
        let checker = NSSpellChecker.shared
        let available = Set(checker.availableLanguages)
        let languages = ["en", "en_US", "en_GB", "fr", "fr_FR"].filter { available.contains($0) }
        var known = Set<String>()
        for word in words where word.count >= 2 {
            let lower = word.lowercased()
            for language in languages {
                let range = checker.checkSpelling(of: lower, startingAt: 0, language: language,
                                                  wrap: false, inSpellDocumentWithTag: 0, wordCount: nil)
                if range.location == NSNotFound {
                    known.insert(word)
                    break
                }
            }
        }
        return known
    }
}
