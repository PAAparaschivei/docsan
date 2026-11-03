# Architecture

**DocSan separates proposed redactions, human approval, output verification and export.**

The native macOS app coordinates a Python engine and local image helpers. The app and its bundled helpers run in the App Sandbox without network entitlements. Dependencies are downloaded during setup, before document processing.

## From input to export

1. The app copies selected inputs into its private container and records their identities. Inputs are retained for review rather than edited in place.
2. The engine reads supported document components. Profile terms and fixed patterns propose replacements; unfamiliar words and visual content can require review. Unsupported or incompletely inspected components block processing.
3. The user reviews words, pictures and PDF pages. Approvals refer to the current content and mask plan; changing a plan invalidates its approval.
4. Writers rebuild supported outputs. Word files use fresh packages; PDF pages are flattened with a sanitized text layer; images are masked and written without their original metadata.
5. A separate verifier inspects the resulting file and checks declared content and metadata. Canary and mutation tests exercise particular failure paths.
6. Export uses the verified file hashes and rejects changed or stale content. The app's current atomic-export implementation requires the destination to be on the same filesystem as its staging area.

## Responsibilities

| Location | Responsibility |
|---|---|
| `app/DocSan/` | Native review interface, profile management, engine coordination and export |
| `app/Helpers/docsan-vision/` | Apple Vision OCR, PDF drawing and image operations |
| `engine/src/docsan/` | Profile rules, format readers/writers, review plans and verification |
| `engine/tests/` | Synthetic document generators, regression cases and failure-injection checks |
| `app/scripts/` | Local app dependency bundling and build integration |
| `scripts/` | Public source selection, privacy checks and documentation validation |

Tesseract is a second local OCR source. It and Apple Vision can still miss content, including the same difficult text. Separating verifier code does not establish complete detection or eliminate correlated OCR errors.

## Private state

The installed app keeps documents and profiles in its sandbox container. The standalone engine defaults to local Application Support storage and accepts explicit output paths. Generated examples use an ignored `build/` directory. A production profile, document, mapping, cache or report must never be placed in an issue, fixture or public repository.

The public source exporter selects named files and source directories. It excludes Git history, private evidence, generated artifacts and internal project records. Pattern checks supplement a review of that exact file set; they cannot identify every possible confidential fact.

## Format contracts

The [Word](docx-component-contract.md), [PDF](pdf-component-contract.md) and [image](image-component-contract.md) contracts describe component-level handling. The [limitations](limitations.md) define the boundary of the public claims.
