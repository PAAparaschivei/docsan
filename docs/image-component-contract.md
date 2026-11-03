# Image component contract

**Every part of a PNG or JPEG file is redrawn, removed or blocks release, and no image leaves without the owner confirming its masks.**

Last reviewed: 2026-10-15. Status: Milestone 2, PNG and JPEG files and pictures inside Word files ([DOCX component contract](docx-component-contract.md)).

The sanitizer never edits the original file. It redraws the first frame into a fresh 8-bit sRGB canvas, paints solid black masks, then writes a new file: a PNG holds a header, unfiltered pixel rows and the end marker; a JPEG holds tables, one frame, its scans and the end marker. The type comes from the file's content, and the output keeps it (D-005).

## File components

| Component | Rule |
|---|---|
| Pixels of the first frame | Read; redrawn upright and masked |
| EXIF orientation | Applied to the pixels; the tag is not carried |
| Further frames (animated PNG, multi-picture JPEG, depth maps) | Removed |
| Metadata: EXIF, GPS, XMP, IPTC, PNG text chunks, comments, colour profiles, embedded thumbnails | Removed |
| Colour stored under fully transparent pixels | Cleared |
| Bytes after the end of the image | Removed |
| A file that is not PNG or JPEG by content, is cut short or exceeds 120 megapixels | Blocks |

## Content

| Content | Rule |
|---|---|
| Text Apple Vision reads | Matched with the same rules as document text; every match is masked over its whole word |
| Unfamiliar words Vision reads | Same review as documents (D-014); a word already under a mask is not asked |
| Vertical text | Read by passes on the image turned a quarter each way |
| Text Vision cannot read: logos, handwriting, stylised or tiny text | Covered by the owner's confirmation; the owner can add masks |
| Logos | In an image file, owner masks. In a Word file, the picture is removed whole when it is a logo (D-025, below); a logo inside a larger picture is masked by the owner |
| Letter case | OCR gets case wrong in drawn lettering and small capitals, so a case-sensitive term ("AGL") also matches in any case but all lower case ("Agl", not "agl"), here and in the verifier's check |

Vision reads each image four times: upright with and without language correction, then turned a quarter clockwise and anticlockwise. Vision shrinks a large image before reading it, and text a few pixels high is lost, so an image wider or taller than 1,536 pixels is also read in overlapping tiles of that size, with the same four passes on each tile. Tiles overlap by 384 pixels, so a word up to that long is whole in one of them. Where several tiles read the same line, the reading most passes agree on is kept; a line that stops at a tile edge inside the image is kept only for masking. Each line keeps up to three readings, and a name is masked when any reading contains it. One exception: when a pass's best reading shows an allowed use, such as the HTTP method in `PATCH /orders`, a weaker reading that dropped the context does not mask it. A Vision reading that has not answered within a minute is cancelled and tried once more on the processor alone; if that also stalls, the image cannot be inspected and blocks release.

Vision's word boxes are loose on one side and can miss a swash on the other, so each mask is fitted to the ink. Along the line, up to 60% of the text height past the box, the helper finds runs of ink columns and covers every run that touches the middle of the box or lies mostly inside it; across the line it follows ink up to 30% of the text height. A 2-pixel margin covers anti-aliasing. The mask hugs the word and leaves neighbouring words intact; a swash that touches the next word masks both rather than leaving part of the name.

## Pictures inside Word files

Each picture gets the same treatment as an image file. A crop set in Word is applied first, so OCR, masks and the output cover only the visible part. A floating picture keeps its position and wrapping (D-023). Words read in a picture join the document's review list. The owner confirms each picture separately; its confirmation is keyed by the picture's position in the file. A masked JPEG stays JPEG; every other raster format becomes PNG.

## Logos in Word files

A logo is removed whole (D-025): masking its name leaves the rest of the mark, which still identifies the company. A picture is removed, leaving nothing in its place, when:

| Rule | Why |
|---|---|
| It sits in a header or footer | Templates put their logos and brand bands there; the document's content is in the body |
| It matches a logo the profile remembers | Every picture exported removed is remembered |
| It matches another picture removed from the same file | The cover often repeats the header's logo at another size |
| The owner removes it | Any picture, from its panel |

The owner can keep any removed picture; it is then masked and confirmed like the rest. A removed picture needs no confirmation, because nothing of it reaches the output. A picture where DocSan reads a company reference and no more than eight words is flagged as a likely logo, with the removal offered first.

Pictures are compared by a fingerprint of what they draw: the helper flattens the picture on white in grey, cuts it to its ink, scales it to 32 by 32 pixels and keeps the sign of each of the 63 lowest cosine frequencies against their median, plus the ink's width over its height. Two pictures match when at most 10 bits differ and their shapes are within 15%. A new size, margin, compression or slight colour change barely moves the fingerprint (0 to 4 bits in the tests); unrelated pictures differ by 16 bits or more, and a diagram by 26 or more. Fingerprints, not pictures, are kept in the profile (`logos`).

## Drawings

EMF drawings in Word files are drawn by DocSan's helper, which replays the drawing records (shapes, paths, curves, text, bitmaps, gradients, hatched and bitmap brushes, clip regions and the coordinate transforms) at 220 dots per inch. Each character is placed where the drawing's own spacing array puts it, so a font that is not installed cannot push a label out of its box or onto a neighbour. Each piece of text is reported with the box of every character, so a name in a drawing is masked from the drawing's own text even when it is too small or too stylised for OCR; OCR still reads text inside bitmaps.

A drawing that DocSan cannot draw completely is refused, because an incomplete diagram misstates the original: one that uses a record the helper does not replay (such as a flood fill or a masked copy), or one stored only as EMF+ whose plain EMF records are a stub. The file then needs the placeholder option. On a test diagram with gradients, curves, dashes, arrows and rotated text, DocSan's drawing matches LibreOffice's within one pixel for 99.8% of the ink. WMF and SVG-only pictures are not drawn yet.

## Owner confirmation

Every image waits for the owner. The engine reports the proposed masks with a digest of the source image and the full mask list. Release requires that exact digest. Any change, such as an added mask, a new profile term that adds a mask or a different file, asks again.

## Verification

The verifier is independent of the sanitizer and uses only the Python standard library plus Tesseract, a second OCR engine. It refuses any chunk, segment or row filter the writer never emits, checks that fully transparent pixels hold no colour, and reads the text with Tesseract's sparse-text layout, with English and French language data, through its own term and pattern rules. It confirms that Tesseract read an image of the file's size. Before a clean result counts, it plants a random name in the file's structure and in an image of the name drawn from stored letter shapes, and must find both.

Tesseract misses some small text that Vision reads, so it is a second check, not the main detector. Neither engine approves an image (D-009).

## Known limits

Text in an image can escape both engines: decorative or very small lettering, low contrast, text at an angle other than a quarter turn, and logos. That is why every image needs the owner's confirmation. The tests that prove the uncorrected and alternate passes depend on how today's Vision model reads two script fonts; `engine/tests/tools/find_ocr_fixtures.py` finds new cases after a macOS update.


## Review workflow corrections (20 October 2026)

The page approval records its current file/page/mask digest without running OCR again. The app can approve a whole PDF after an explicit whole-document review; the approval is bound to the page-digest snapshot captured when that action opens. Export still reruns the engine, rejects changed plans and independently verifies the saved artifact. "Reviewed" is distinct from "Verified".

The area-mask editor accepts left, top, width and height as percentages of the current page. It clips masks to the image bounds and invalidates the approval when a mask changes. Pointer drawing remains available.

PDF planning masks long account-like IDs, explicit customer/name and postal-address fields, detected barcodes and small coloured marks next to company lettering in a page corner. Unrecognized logos and context still need review. The helper's local `regions` request accepts an image path and returns barcode bounds and connected colour-component bounds in upright image pixels, without decoded payloads; the engine makes the masking decisions. Helper-build hashes invalidate cached observations after updates. The independent verifier has its own identifier and numeric-context rules. All PDF person masks use full labels in the hidden text layer so masked first names do not remain searchable.


## Small codes and footer fragments

The local `regions` request now also returns `code_candidates`, a list of
upright pixel bounds for isolated near-square dense grayscale ink with frequent
transitions along both axes. It returns no decoded data. The engine masks these
regions even when Vision cannot decode them. Ordinary text and outlined icons
are negative fixtures. This is a geometric heuristic; visually ambiguous marks
still need review. The same checks apply to standalone images and Word pictures,
including the image crop, as well as PDF pages. Final-image verification checks
for surviving readable codes and code-like geometry.

A sensitive match in small print in the bottom tenth of a page/image expands to
its footer row and a bounded area below it, covering clipped glyph fragments.
Expansion stops before neighboring larger text and does not alter unrelated
page numbering. Text mask fitting trims empty vertical line space before adding
the existing anti-aliasing margin. Approval remains bound to the resulting mask
plan and final verification remains mandatory.

The `fit` request accepts an optional `guards` list aligned with `boxes`; each
entry holds nearby unmatched word rectangles. Fitting stops at the intervening
ink gap to protect neighboring words when OCR bounds are loose. Conflicting
guards fall back to the wider mask. Matrix geometry uses two contrast thresholds
and requires predominantly grayscale pixels to preserve colored artwork.


## Screenshot identity fields

Deterministic layout rules cover browser bookmark strips only when OCR finds a top address line and at least three horizontally distributed labels below it. Email toolbar cues identify recipient headers; publication timestamps identify comment-author headers; an assignee table heading identifies the private column. These masks cover avatars without introducing a face model. Network and username table labels mask the values across multiple columns. Subscription-owner values are personal fields. A legal-company suffix can identify a private breadcrumb or filter value.

The assignee mask begins at the heading's left edge and stops before the next column. Email subjects and message bodies stay outside recipient masks. Brand masks beside sensitive corner lettering stop at neighboring text above and below, including when an icon joins a larger colored header. Synthetic tests check these neighboring fields explicitly. These are layout heuristics with visual review, not a general UI detector or a universal anonymity guarantee.

IPv4 values and short private-host identifiers in server/transfer/snapshot context receive identifier masks. The verifier uses its own expressions for these classes. Explicit version/release prefixes distinguish dotted version strings. A leading-dot domain suffix is matched, and a one-letter email-domain fragment is covered in OCR mode. General entity recall, unsupported layouts and unreadable fragments remain review cases.


## Final raster OCR resolution

Word pictures and standalone images are enlarged by the verifier's existing bounded resampling routine before Tesseract reads them, as PDF pages already are. This improves small-text recognition without changing output pixels or excluding dates, identifiers or terms from checks. The verifier checks the enlarged dimensions, maps findings back into the original image coordinates and refuses release if resizing or OCR fails. Synthetic cases preserve small calendar text while still detecting a small sensitive name and a genuine account number.

A one/two-character OCR guess can be ignored only when its original PNG box is at most 4 by 4 pixels and independent pixel decoding proves the whole patch, including a one-pixel border, is opaque and each RGB channel varies by at most one level. This handles raster background noise that OCR reports as letters. The rule does not apply to JPEGs, larger boxes, unknown pixels, transparency or even faint real strokes. Sensitive profile terms remain active.

Saved-page follow-ups (21 October 2026): hash-prefixed references with at least seven digits, optionally separated by spaces, receive ID masks and independent residual checks. Short-host context includes VM, machine, database, production and backup labels. Nonstandard host aliases need explicit private-profile classification. Whole public PostgreSQL/Postgres words survive a partial short-token collision; an explicit full-name rule still applies. Native learned-word editing exposes DOMAIN, ADDRESS and ID. These bounded corrections do not establish recall for arbitrary unseen identifiers.

### Cropped screenshot context

A cropped picture may also use full-image OCR alternatives for matching, restricted to lines wholly inside the visible rectangle. Mismatched dimensions block processing. Alternatives do not displace the cropped preview reading. Repeated aligned transport fields can type a clipped continuation as a host value; masks are bounded to the observed line and fitted to ink. Such continuations do not seed legal-footer expansion. Public System Variables are distinguished by their column header and position, while links and explicit profile terms remain checked.


Remote-session screenshots use public menu and command rows to bound title and tab identity bands. Those bands are masked while the public menu, command controls and application body remain visible. Final-image OCR independently checks text in those bands. Minor OCR errors in a small public interface vocabulary can be omitted from unfamiliar-word prompts only inside verified UI roles; this does not modify the source image, save profile entries or disable explicit company matching. Code familiarity and image masking remain separate checks.

Document-local host facts use the normal bounded term-separator matcher, including dot, underscore and compact variants, without profile writes. The independent verifier reconstructs physical OCR rows from nearby boxes and retains every fragment. Public-variable recognition requires a System Variables header and the expected column; the bounded OCR tolerance keeps the final dotted component fixed. Reserved SQL qualifiers and public logger namespaces can cross extraction fragments in their typed contexts. Actual links, unrelated domains and explicit company rules remain findings. Synthetic cases cover these limits.

Raw Word XML code recognition is bounded to a single emitted text element. XML attribute delimiters and apostrophes in other paragraphs cannot classify a public SQL qualifier as a quoted domain. Raw attribute values receive domain checks, as do quoted SQL literals and real links; explicit company-rule scans remain separate.
