# Configuration

**Import a private company profile to tell DocSan which references to replace and which words to keep.**

The [starter template](../examples/profile-template.json) is valid JSON accepted by the app and engine. All names, addresses and domains in it are invented. It is separate from the smaller [synthetic demo profile](../examples/profile.json).

## Prepare and load a profile

1. Copy the starter template into a private folder outside the Git checkout and outside cloud sync. Edit that copy locally, without a cloud-connected agent reading its contents or screen.
2. Replace the fictional entries with the organization's names, aliases, acronyms, people, products, projects, systems, addresses and domains that must be removed. Add spelling variants and standalone surnames where needed. Remove unused example entries.
3. Keep `redact_system_names: true` and `person_style: "label"` when listed system names and full person-name replacement are required. These template settings are stricter than the app's new-profile defaults.
4. In the app's Drop screen, choose **Set Up… → Import Profile File…**, then select your JSON file. If a profile is already loaded, use **Change… → Import Profile File…**. Invalid or unknown fields are rejected. The app copies the file into its sandbox; editing your source copy later does not change the imported copy until you import it again.
5. Try a made-up document containing your test cases. Review the proposed text and visual masks, resolve unfamiliar words and export only after verification. A valid profile establishes syntax, not complete detection.

The profile name appears on the Drop screen. **Options → Learned words** changes saved word decisions. The app also exposes person-name style and system-name policy controls. Review decisions update the app's copy, not the original imported file.

## Top-level fields

| Field | Meaning |
|---|---|
| `format` | Required literal `"docsan-profile"` |
| `version` | Required profile format version `1` |
| `name` | Required non-empty display name. It does not automatically create a matching rule; put the company name in `terms` too |
| `policy` | Required non-empty revision tag for your profile, such as `"company-v1"`. It records a version, not a choice of built-in rule sets |
| `terms` | Required, non-empty list of references and replacement labels |
| `exceptions` | Optional list of contextual exemptions for exact term texts. Default `[]`; exemptions leave matching text readable |
| `safe_words` | Optional list of approved public words, default `[]`. Used by unfamiliar-word review and some domain/host heuristics. Adding identifying text here can reduce detection; approve only words that may remain readable |
| `redact_system_names` | Optional boolean. The engine/new-profile default is `false`, which keeps `SYSTEM` terms. The starter template sets `true` |
| `person_style` | Optional `"first_name_initial"` or `"label"`. The default keeps the first name and surname initial in text, such as `Elena M.`. The template uses full labels such as `[PERSON-1]`. Image/PDF person-name areas are masked |
| `logos` | Optional list of learned image fingerprints. Start with `[]`; the app records fingerprints of pictures removed as logos during export. Do not invent these values |

JSON has no comments. Do not add explanatory keys, API credentials, output folders or environment-variable placeholders: the loader does not expand `${VARIABLE}` strings. Profile syntax is defined by [the loader](../engine/src/docsan/profile.py) and read independently by [the verifier](../engine/src/docsan/verify.py).

## Term fields and replacement labels

| Field | Meaning |
|---|---|
| `text` | Required non-empty reference to match |
| `label` | Required label from the list below |
| `case_sensitive` | Optional boolean, default `false`. Set `true` for an uppercase acronym that should not match ordinary lowercase text |
| `whole_word` | Optional boolean, default `false`. Set `true` to require a word boundary rather than matching inside a larger token |
| `group` | Optional string grouping aliases under one replacement identity, for example a full name and surname |

Supported labels: `CLIENT`, `ORG`, `PERSON`, `SYSTEM`, `PROJECT`, `SITE`, `PRODUCT`, `LABEL`, `ADDRESS` and `DOMAIN`. `CLIENT` and `LABEL` use unnumbered labels. Other identities receive numbered labels, subject to the selected person-name style. Matching also normalizes common text/OCR variations; literal text and a short acronym can still need different rules.

An advanced exception has `text` equal to one term's text and a non-empty `followed_by` list of regular-expression strings. Use exceptions only when a specific context must remain readable and test both the exempt and non-exempt forms. The starter includes no exceptions.

## Built-in behavior

Profiles configure listed references and review choices. Fixed engine rules also inspect email addresses, URLs/domains, phone numbers and supported structured identifiers. They are not arbitrary user-supplied regex rules. Images and PDF pages still require visual review; unfamiliar logos and identifying context are not made safe by loading a template. See the [limits](limitations.md) and format contracts before sharing.

## Standalone CLI settings

```bash
cd engine
uv run --locked docsan run INPUT.docx --profile /LOCAL/PRIVATE/company.json --lexicon /LOCAL/PRIVATE/wordlists --private /LOCAL/PRIVATE/work --out /LOCAL/EXPORT
```

The paths above are placeholders to replace locally. `--profile` selects the JSON file; `--lexicon` selects local word lists; `--private` selects staging and reports; `--out` selects the export destination. The CLI can update the supplied profile when saving classifications. Use a private working copy. PDFs stop for required page review in the native app.

| Variable | Standalone engine use |
|---|---|
| `DOCSAN_VISION` | Path to the local `docsan-vision` executable for image/PDF processing |
| `DOCSAN_TESSERACT` | Optional path to the local Tesseract executable; otherwise the engine checks its bundled/default locations |
| `DOCSAN_TESSDATA` | Optional path to local English/French OCR model data; otherwise bundled/default locations are used |

The native app bundles these components and sets a restricted helper environment. You do not set these variables or install a provider SDK to use the built app. The standalone CLI is not protected by the native app's sandbox. Read its [setup instructions](../engine/README.md) and the [privacy boundary](privacy.md).
