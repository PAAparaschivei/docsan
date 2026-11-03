# DocSan engine

**The offline processing engine: reads a Word file, PDF or image, removes company references, verifies the result independently and releases only verified bytes.**

The [architecture](../docs/architecture.md) describes the review and export boundaries. The supported parts of each format are listed in the [DOCX](../docs/docx-component-contract.md), [PDF](../docs/pdf-component-contract.md) and [image](../docs/image-component-contract.md) component contracts. Read the [limitations](../docs/limitations.md) before using the output.

## Use

```bash
uv run docsan run INPUT... --profile PROFILE.json --out SHARE_FOLDER --lexicon WORDLISTS
uv run docsan classify --profile PROFILE.json Halden=SITE setup.py=safe
uv run docsan verify FILE.docx --profile PROFILE.json   # or FILE.pdf, FILE.png, FILE.jpg
```

Profiles, word lists, staging and reports are private and stay outside this repository. The default private folder is `~/Library/Application Support/DocSan`.

PDFs and images need the Vision helper, which reads and masks pictures and draws PDF pages; set `DOCSAN_VISION` to a build of `app/Helpers/docsan-vision` (the tests build one). On the command line a PDF stops at "needs review", because each page's masks are confirmed in the app.

## Test

Prepare the same English/French OCR data the app uses, then run the suite:

```bash
cd ..
app/scripts/fetch-tessdata.sh
cd engine
uv sync --locked
uv run pytest
```

Run on macOS 26 or later on Apple silicon with Tesseract and the Xcode command line tools installed. The English-only OCR fallback is useful for limited evaluation but does not match the acceptance-suite configuration. LibreOffice enables the optional render-comparison cases.

The leak matrices build synthetic files for a fictional company: Word files in `tests/leakmatrix.py`, images in `tests/imagematrix.py` and PDFs in `tests/pdfmatrix.py` (made by Quartz through `tests/tools/make-pdf.swift`, with parts added by `tests/pdfedit.py`). A passing suite shows that the declared components are handled; it does not prove that arbitrary documents are safe.
