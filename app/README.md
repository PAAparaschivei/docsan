# DocSan app

**The SwiftUI Mac app. It runs the engine as a sandboxed helper process: files are copied into the app's private container, checked there and published only after verification.**

## Build and run

Start with the complete [requirements](../README.md#requirements): an Apple silicon Mac, macOS 26+, full Xcode, Homebrew, uv, XcodeGen and Tesseract. After the build, the app bundles its runtime and OCR dependencies and needs no provider account or API key. Import the [starter profile](../examples/profile-template.json) using the [configuration guide](../docs/configuration.md).

Runtime dependency versions come from `engine/uv.lock`. The bundle script exports only runtime dependencies, verifies their hashes and installs the DocSan wheel without resolving a second dependency set. This keeps local app builds aligned with the engine test environment. Bundled-app redistribution still requires the [licensing and release checks](../docs/releasing.md).

```bash
uv python install 3.13          # once: a relocatable CPython build
brew install tesseract          # once: the image verifier's OCR
scripts/bundle-engine.sh        # runtime, engine, Vision helper, Tesseract and its English and French data
xcodegen generate
xcodebuild -project DocSan.xcodeproj -scheme DocSan -configuration Debug -derivedDataPath build/DerivedData build
open build/DerivedData/Build/Products/Debug/DocSan.app
```

`project.yml` is the source of truth; the Xcode project is generated and not committed. The build phase `scripts/embed-engine.sh` copies the bundle into the app and signs every binary; the interpreter is signed with `scripts/engine-helper.entitlements` so it inherits the app's sandbox. The app has no network entitlement, so neither it nor the engine can open a connection. Builds are signed ad hoc for local use.

The sandbox does not stop a separate cloud-connected agent from receiving screenshots or text it can access. Read the [privacy boundary](../docs/privacy.md) before processing confidential inputs.

## How the app uses the engine

| Step | What happens |
|---|---|
| Profile | Imported or created through the engine; kept at `profiles/active.json` in the container |
| Drop | Files are copied into `inbox/` in the container, then the engine runs a dry run (`analyze`) |
| PDFs | The helper draws every page with PDFKit (`Helpers/docsan-vision/PDF.swift`) at 200 dots per inch, and again at 400 for reading only, and reads its text layer and structure; Vision reads each page in overlapping tiles, then once more with its masks burned in; the engine plans each page's masks with its own digest. The review shows the pages one after another, each with its masks; open a page to confirm it (Confirm Page). Export writes a flattened PDF ([PDF contract](../docs/pdf-component-contract.md)) |
| Images | The Vision helper (`Helpers/docsan-vision`) reads the text; the engine plans masks. Each image shows its masks; drag to add one. Confirm Image sends the plan's digest back, and only that exact plan is released. In a Word file, pictures in headers and footers and pictures that match a known logo are removed whole (D-025); Remove Picture and Keep It change that per picture |
| Review | Words in the technical glossary, or whose lowercase form the macOS spell checker knows in English or French, are familiar. The rest appear on one list, grouped as possible names, acronyms and other words, with Keep as the default. One Apply saves every decision to the profile (`classify`) and checks the batch again |
| Export | The engine releases verified files into `outbox/`; the app checks each hash again and hard-links the files into a new folder inside the folder you choose, with `summary.json` |

The container is `~/Library/Containers/com.aparaschivei.docsan/Data/Library/Application Support/DocSan`. Export refuses a folder on another disk, because files could not be placed there in one atomic step.

Every helper gets `CFFIXED_USER_HOME`, which points system frameworks at the container. Without it Vision looks for its model cache in the real home folder, the sandbox refuses, and text recognition fails with `nilError`. The helper also embeds an `Info.plist` with a bundle identifier.

The macOS spell checker is the word source for the review (decision D-020). Words a user has taught the spell checker also count as known.

The list groups words as possible names (they start as Redact, as people), products and systems (kept as system names), acronyms and codes, places and other words (kept). Options → Learned words shows every word the profile keeps or redacts and changes them. The toolbar search (⌘F) finds a word or phrase across the batch: ‹ › (Return, ⌘G, ⇧⌘G) show each occurrence in the document or picture, the bar counts it in the originals and in the output, and Keep or Replace as saves the choice in the profile and checks the batch again.

Diagrams in Word files (D-022): EMF drawings such as Visio diagrams are drawn by the helper (`Helpers/docsan-vision/EMF.swift`) and confirmed like pictures, with their own text matched exactly; Visio and other embedded objects become their masked preview; charts become tables and SmartArt becomes lists in the text. Options → Pictures and diagrams offers three choices: keep them with names masked (the default), keep them and label only what DocSan can't read, or replace them all with labels.

The review screen shows each file as pages (D-024): the engine describes the original and the sanitized file from the same model the output is written from (`docsan.docx.pages`), with the proposed masks burned into the pictures, and `Screens/PagesView.swift` lays the description out with the macOS text system into pages of each section's size, with headers, footers, page numbers, list numbers, tables and floating drawings. The Text view beside it keeps word search and decisions.

The toolbar's appearance button switches between Light, Dark and Match System; the choice is remembered, and document pages stay light either way.

In the list, ↑ and ↓ move between words, R marks a word for redaction (as ORG), P as a person, S as a system name and K keeps it. Each group's Redact all button, or ⇧R, marks the whole group: possible names as PERSON, the other groups as ORG, with the other labels in its menu. ⌘↩ applies.

## Self-test

```bash
build/DerivedData/Build/Products/Debug/DocSan.app/Contents/MacOS/DocSan --selftest --snapshots
```

This runs the whole flow inside the sandbox, in its own storage folder (`DocSan-selftest`) and settings, on files placed in the container's `tmp/selftest/` folder (`profile.json`, `in/`, optional `decisions.json`), prints a JSON result and, with `--snapshots`, captures every screen in light and dark. With a PDF among the inputs it also captures its pages and one page under review (`2d`, `2e`, `3f`). The macOS 26 glass sidebar and the decision panel's scrolling content cannot be captured, so the sidebar rows and the picture and page panels are rendered separately.

## Notes

- The decision panel is a fixed column, not SwiftUI's `.inspector`. On macOS 27 an inspector in the detail column of this split view triggers an Auto Layout loop.
- Colours: greys carry the interface through a graphite accent; green, orange and red appear only for file states, always with a symbol and a word.


## Review workflow corrections (20 October 2026)

The page approval records its current file/page/mask digest without running OCR again. The app can approve a whole PDF after an explicit whole-document review; the approval is bound to the page-digest snapshot captured when that action opens. Export still reruns the engine, rejects changed plans and independently verifies the saved artifact. "Reviewed" is distinct from "Verified".

The area-mask editor accepts left, top, width and height as percentages of the current page. It clips masks to the image bounds and invalidates the approval when a mask changes. Pointer drawing remains available.

PDF planning masks long account-like IDs, explicit customer/name and postal-address fields, detected barcodes and small coloured marks next to company lettering in a page corner. Unrecognized logos and context still need review. The helper's local `regions` request accepts an image path and returns barcode bounds and connected colour-component bounds in upright image pixels, without decoded payloads; the engine makes the masking decisions. Helper-build hashes invalidate cached observations after updates. The independent verifier has its own identifier and numeric-context rules. All PDF person masks use full labels in the hidden text layer so masked first names do not remain searchable.


For an isolated synthetic run, `--selftest-inputs PATH` selects a separate input/evidence directory inside the app container. It contains `profile.json`, `in/` and optional `decisions.json`; `out/`, `result.json` and optional snapshots are written beside them. The self-test still uses `DocSan-selftest` storage, never the user's active profile. Its result includes the stale-target rejection check and approval time.

The app now reports the current PDF page during mask planning and a separate saved-PDF verification stage. Progress contains no document text or filenames and stays in private storage.
