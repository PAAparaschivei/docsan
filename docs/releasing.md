# Preparing a release

**Release only an inspected source snapshot, with reproducible checks and an explicit record of remaining limits.**

## Public source

Run these commands from the repository root:

```bash
python3 -m unittest discover -s scripts -p 'test_*.py'
python3 scripts/check_links.py
python3 scripts/public_source.py --report build/public-source-manifest.json --export dist/docsan-source
python3 scripts/public_source.py --root dist/docsan-source --tree
```

The exporter selects source, synthetic fixtures, community files and public documentation. It does not recursively copy the working directory. It refuses credential patterns, protected or generated paths, unreviewed document/image files, symlinks and files that change after inspection. It excludes Git history and internal project records. Reports contain paths, finding categories and file hashes, never matched credential values.

Review the manifest and the exact exported files. Pattern checks cannot recognize every private fact. Confirm that examples are invented, assets have known origins and metadata contains only intentional public attribution. A filename or a negative scan is not sufficient evidence that an arbitrary document is safe to publish.

From the export, run the documented synthetic example, the engine suite and the local app build. Preserve the test results and the source archive hash outside the public file set. Record skipped tests and environmental failures rather than describing them as passes.

If preserving an existing repository's history, inspect all reachable branches, tags, commit messages, author metadata and file blobs before changing visibility. A reviewed source export avoids carrying that history into a new public snapshot; it does not remove information from an existing remote. Rewriting or replacing remote history requires an explicit publication decision.

## GitHub setup

Use a concise description such as: "Local document redaction for macOS, with profile rules, human review and separate output verification."

Suggested topics: `macos`, `document-redaction`, `privacy`, `swiftui`, `python`, `docx` and `pdf`. Enable private vulnerability reporting before publication and confirm its reporting link works. Enable GitHub secret scanning and push protection where available. Review the default branch and the exact source snapshot before enabling Actions. The included workflow runs synthetic tests and requires no project secrets.

The workflow's first real push or pull-request run is a publication check. Local execution does not prove the hosted trigger passed. Do not display a passing CI badge before a successful run.

## Downloadable Mac app

A source release does not clear the current bundled app for redistribution. Before offering a binary:

1. Inventory the exact Python runtime, native libraries, OCR tools and language models in the bundle. Include their applicable licence texts and notices.
2. Resolve the libiconv packaging choice and audit the runtime's bundled dependencies, as recorded in [third-party notices](../THIRD-PARTY-NOTICES.md).
3. Build, sign and notarize the intended artifact, then verify it on a separate Mac or fresh user environment. Keep local ad-hoc builds labelled as local evaluation builds.
4. Exercise the installed sandbox, review and export workflow using a fresh synthetic profile and verify the actual release file hashes. Never include private documents or profiles in the bundle.

Sources: [Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0.html), [GitHub runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners) and [Apple app distribution](https://developer.apple.com/macos/distribution/).
