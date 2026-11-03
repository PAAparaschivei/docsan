# Third-party notices

**Source dependencies keep their own licences; distributing a bundled app requires a separate inventory and notice check.**

Source preparation reviewed: 2026-11-03. Bundled-app inventory below was recorded on 2026-10-14 and must be refreshed against the actual release artifact. DocSan's own code is licensed under Apache-2.0 ([LICENSE](LICENSE)). Dependency names and licences do not relicense their upstream code.

## Source release

The reviewed public source export contains DocSan code, synthetic fixture generators, documentation and the project's icon assets. It contains no third-party wheels, Python runtime, OCR executable, language models, fonts or real documents. Setup downloads or uses dependencies separately.

| Dependency | Role | Licence evidence |
|---|---|---|
| lxml 6.1.3 | Runtime XML parsing | BSD-3-Clause in installed distribution metadata and the [upstream licence](https://github.com/lxml/lxml/blob/lxml-6.1.3/LICENSE.txt) |
| pytest 9.1.1 | Development tests | MIT in installed distribution metadata and its included `LICENSE` |
| uv_build | Python build backend | Apache-2.0 OR MIT; [upstream licences](https://github.com/astral-sh/uv) |

The locked transitive development dependencies are recorded in `engine/uv.lock`. They are not vendored into the source export and remain under their own licences. The engine's wheel and source distribution include copies of DocSan's `LICENSE` and `NOTICE`; the public-source check rejects copies that differ from the repository originals.

The icon is an original project asset generated for DocSan, rather than an imported company mark. Test lexicons and profiles contain invented examples; actual company profiles are excluded. No source licence grants permission to redistribute someone else's documents, fonts, logos or other assets used as input.

## Bundled-app inventory and open checks

This table describes the existing local bundling approach, not a cleared binary release. "Checked" records the earlier licence or metadata inspection; "To check" is an unresolved distribution item.

| Component | Used for | Licence | Shipped in the app | Status |
|---|---|---|---|---|
| lxml 6.1.3 | Reading Word XML | BSD-3-Clause | Yes, from Stage 4 | Checked |
| libxml2, libxslt, libexslt (inside the lxml wheel) | XML parsing | MIT | Yes, with lxml | Checked |
| zlib (inside the lxml wheel) | Compression | zlib licence | Yes, with lxml | Checked |
| GNU libiconv (inside the lxml wheel) | Character conversion | LGPL-2.1 | Yes, with lxml | Checked; needs a packaging decision |
| Python 3.13 runtime (python-build-standalone) | Running the engine inside the app | PSF-2.0, plus the licences of the libraries it bundles | Yes | To check |
| Tesseract 5.5.2 | Second OCR in the image verifier (D-009) | Apache-2.0 | Yes, from the Homebrew build | Checked from package metadata |
| Tesseract English and French data (tessdata_fast 4.1.0) | Reading English and French text in the verifier | Apache-2.0 | Yes; French is downloaded by `app/scripts/fetch-tessdata.sh` and checked against a pinned SHA-256 | Checked from the repository's licence file |
| Leptonica 1.87 | Image reading for Tesseract | BSD-2-Clause | Yes, with Tesseract | Checked from package metadata |
| libarchive 3.8 | Loading Tesseract data | BSD-2-Clause | Yes, with Tesseract | Checked from package metadata |
| libpng, libjpeg-turbo, libtiff, giflib, libwebp, OpenJPEG | Image formats for Leptonica | libpng-2.0; IJG, zlib and BSD-3-Clause; libtiff; MIT; BSD-3-Clause; BSD-2-Clause | Yes, with Tesseract | Checked from package metadata |
| lz4, liblzma, zstd, libb2 | Compression for libarchive | BSD-2-Clause; 0BSD; BSD-3-Clause (of its BSD or GPL-2.0 choice); CC0-1.0 | Yes, with Tesseract | Checked from package metadata |
| pytest 9.1.1 | Tests | MIT | No, development only | Checked |

**Licence texts:** before the app is distributed, the licence file of every shipped component goes into the app bundle. Tesseract links libcurl from macOS for reading web addresses; the app's sandbox has no network entitlement, so it cannot connect.

**Packaging decision for libiconv:** the prebuilt lxml wheel links libiconv statically. Before the app ships, either build lxml against the libiconv and libxml2 that macOS provides, or meet the LGPL-2.1 terms for replacing the library.

## Planned

| Component | Planned use | Expected licence | Status |
|---|---|---|---|
| LibreOffice | Making EMF and WMF test drawings (tests only); legacy Word conversion is open (O-006) because it cannot run inside the App Sandbox | MPL-2.0 | Not shipped |

Apple Vision, ImageIO, PDFKit, `textutil`, `sips` (tests only) and the macOS spell checker (the review's word source, D-020) are part of macOS and are not shipped with DocSan. Image masks are painted with Core Graphics, so Pillow is no longer planned.

PDFs add no dependency. PDFKit and Core Graphics, part of macOS, read and draw PDF pages inside the sandbox; DocSan's own Python code writes the flattened output and the verifier parses it with the standard library. The output's text layer names the standard Helvetica font without embedding any font file. pypdfium2, pdfminer.six and ReportLab, planned earlier, are therefore not used, and PyMuPDF and MuPDF stay excluded (AGPL).
