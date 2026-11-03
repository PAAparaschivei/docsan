# Limitations and review responsibilities

**A verified output still needs human review before sharing. DocSan does not guarantee anonymity or removal of every identifying reference.**

## Detection

Profiles provide explicit terms, replacements and word decisions. A name that is also an ordinary word can remain unless the profile identifies it. OCR can miss tiny, faint, clipped, rotated or stylized text. Logos, barcodes and unfamiliar visual identifiers can need a manually drawn mask or removal of the whole picture.

The verifier checks supported content independently from the sanitizer, but it still depends on parsers, rules and OCR. A passing check means that those checks found no remaining findings; it does not prove that an outside reader cannot identify the source.

## Context and preservation

Dates, figures, locations, distinctive architecture and combinations of facts can identify an organization even after its name is removed. The user must decide whether that context is authorized for sharing. Removing names does not change confidentiality obligations.

Supported Word formatting is rebuilt rather than copied as an opaque original package. Complex drawings can be masked, converted to simpler representations or refused. Charts become tables and SmartArt becomes lists. Review the output where visual fidelity matters.

PDF pages become raster images with a sanitized text layer. Output files can grow substantially; original editing, links, forms, annotations and accessibility structure are not preserved. PNG and JPEG output lose original metadata.

## Unsupported and incomplete cases

- PowerPoint and Excel files are outside the current input scope.
- Legacy `.doc`, `.rtf` and `.odt` conversion is outside the current app workflow.
- Encrypted PDFs, XFA forms and PDF portfolios are unsupported.
- Unsupported document components or incomplete inspection should block export. Treat an unexplained successful export of such content as a security report.
- The app requires macOS 26 or later on Apple silicon. The source has no Windows or Linux app support.
- Atomic app export currently requires a destination on the same filesystem as staging.

## What verification establishes

Synthetic regression, canary and mutation tests demonstrate the specific cases they exercise. They do not establish performance on a representative held-out collection of real documents. The source release is for evaluation; no universal leak rate or safety certification is claimed.

Use made-up files when reporting a problem. Follow the [security policy](../SECURITY.md) for a leak or export bypass.
