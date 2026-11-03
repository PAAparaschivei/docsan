# Contributing to DocSan

**Contributions are welcome when they keep DocSan offline, deterministic and tested on made-up documents only.**

## Ground rules

- **Synthetic data only.** Fixtures use the fictional company in `engine/tests/fixtures/profile.json`. Never commit, attach or paste a real document, company profile, replacement mapping or exported file.
- **Local processing.** Detection and replacement use explicit rules. Local OCR is allowed; cloud processing and language models are outside the product's scope.
- **No network.** Nothing in the app, the engine or a helper may open a connection.
- **Every safety step is proven.** A new sanitizer or verifier step is registered in `engine/src/docsan/steps.py` or `engine/src/docsan/verify.py`, and a fixture must fail when the step is switched off. The mutation tests check this.
- **Behaviour changes are documented.** Explain the user-visible change in the pull request and update the matching component contract in `docs/`.

## Set up

```bash
brew install uv xcodegen tesseract
app/scripts/fetch-tessdata.sh
cd engine && uv sync --locked
```

Building the app is described in the [README](README.md#build-and-run).

## Test

```bash
cd engine
uv run pytest            # the full suite, including the slow LibreOffice render checks
```

From the repository root, also run the public-source tooling checks:

```bash
python3 -m unittest discover -s scripts -p 'test_*.py'
python3 scripts/public_source.py
python3 scripts/check_links.py
```

The synthetic suite needs macOS 26 or later on Apple silicon, the Xcode command line tools and the English/French OCR data prepared above. The full acceptance suite uses the same language data as the app; the English-only fallback can produce different results on tiny text. Install LibreOffice to include render-comparison tests; without it those tests skip. The workflow in `.github/workflows/ci.yml` runs on an Apple silicon macOS runner. CI checks synthetic fixtures, not private or arbitrary real-world documents.

The picture tests build the Vision helper from `app/Helpers/docsan-vision` with `swiftc`, so they need Xcode. Render comparisons also need LibreOffice. The app's own end-to-end check runs inside the sandbox:

```bash
app/build/DerivedData/Build/Products/Debug/DocSan.app/Contents/MacOS/DocSan --selftest --snapshots
```

## Pull requests

1. Open an issue first for anything larger than a fix, so the approach can be agreed.
2. Keep one change per pull request, with tests that fail without it.
3. Write commit messages and documentation in plain, short sentences, without em dashes.
4. Confirm in the pull request that every fixture is synthetic and that the tests pass.

By contributing you agree that your contribution is licensed under the [Apache License 2.0](LICENSE).
