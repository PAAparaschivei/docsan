# PDF component contract

**Every part of a PDF is drawn, removed or blocks release, and the output is a fresh flattened file: pictures of the pages under an invisible text layer.**

Last reviewed: 2026-10-15. Status: Milestone PDF. Applies to `.pdf` files; output is always a fresh `.pdf` (D-005, D-008).

The sanitizer never edits the original. Apple's own PDF reader, running in DocSan's Vision helper inside the app sandbox, draws each page as a viewer shows it and reads its text layer; DocSan then writes a new file that holds only a picture of each page, with the confirmed masks burned into its pixels, and an invisible text layer built from the sanitized text. "Drawn" means a part shows on the page pictures and is therefore read by OCR, masked and confirmed with the page. "Removed" means the part is inspected and deliberately not carried: nothing of the original file's structure reaches the output. "Blocks" means the file is not exported.

## Document parts

| Part | Rule |
|---|---|
| Page tree, pages, their content, resources, media and crop boxes, rotation | Drawn: each page is drawn inside its crop box, turned as a viewer shows it |
| Text layer | Read with the box of every character; used for matching, masks and the output's text (see Text below) |
| Pictures (image objects, inline images) | Drawn; text in them is read by OCR and masked |
| Vector drawings (paths, shadings, patterns, form objects, Type 3 fonts) | Drawn; text in them is read by OCR and masked |
| Fonts | Used to draw the pages; not carried. The output names no font but Helvetica |
| Annotations: notes, highlights, stamps, shapes, ink, free text, watermarks, links, widgets, media | Drawn as a viewer shows them; their dictionaries (comments, authors, link targets, attached files, sounds, movies) are removed |
| Redaction annotations that were never applied | Block: the text they mark is still on the page |
| Form fields (AcroForm) | Their visible values are drawn; the fields, their names and values are removed |
| XFA forms | Block: only Adobe software draws them |
| Bookmarks (outline), named destinations, threads, page labels | Removed |
| Optional content (layers) | Drawn as the document shows it by default; a layer hidden by default is left out, and the layers' names are removed |
| Attached files (embedded files, file attachment annotations, associated files) | Removed |
| JavaScript, open actions and additional actions | Removed |
| Document information (title, author, subject, keywords, producer, dates) | Removed; the output's only metadata is "Producer: DocSan" |
| XMP metadata of the document, a page or a picture | Removed |
| Tags, structure tree, alternative text, marked content | Removed |
| Viewer preferences, page layout and mode, language, output intents, page-piece data, thumbnails, transitions, web capture data, digital signatures and permissions | Removed |
| A producer's private keys, written with its registered prefix and a dot (such as `PTEX.FileName` from pdfTeX) | Removed |
| A PDF portfolio (collection) | Blocks: its content is in attached files |
| A PostScript drawing, or content stored in another file (external streams, reference objects) | Blocks: macOS cannot draw it completely |
| Encryption, with or without a password | Blocks: remove the protection and add the file again |
| A file that is not a PDF, is cut short, has a damaged cross-reference table, is larger than 512 MB or has more than 500 pages | Blocks |
| Any key DocSan has no rule for, in the catalog, the name tree, the form, the page tree, a page, its resources, a picture or a form object; any other annotation or object type | Blocks |

Several parts are read only to count them: the file result lists how many bookmarks, attached files, scripts, annotations, form fields and metadata entries were removed, without their content.

## Text

| Content | Rule |
|---|---|
| Every word of a usable text layer | Matched like document text, including words too faint or small for OCR, such as a light grey watermark. Every match is masked over its characters' boxes, line by line, or character by character for text set at an angle; a match none of whose characters leaves any ink gets no mask, because nothing of it shows |
| Text layer words that leave no ink on the page: a hidden layer, white on white, text under an opaque box | Left out of the output's text and of the review, because no one sees them. A word leaves ink when a part of its characters' boxes differs from the box's commonest brightness by more than 10 of 255 levels |
| A text layer that disagrees with the page | When fewer than half of the words Apple Vision reads over the text layer are in it (a font without a character map reads as nonsense), the layer is ignored and Vision's reading is the page's text |
| A page with no text layer (a scan) | Vision's reading is the page's text |
| Text only Vision sees: pictures, drawings, stamps, form values, scanned pages | Matched like image text (every reading of every pass, any letter case for case-sensitive names) and masked |
| A name split across two lines | Lines join up before matching, and a word hyphenated at a line end joins again ("Aure-" and "lan"); each line's part of the match is masked |
| Unfamiliar words | Same review as documents (D-014, D-020), from the text layer and Vision's reading of text outside it |

Vision reads each page from a second drawing at 400 dots per inch, made for reading only, in overlapping tiles of 1,536 pixels as well as whole: labels in a dense diagram can be four or five pixels high on the 200 dpi page, too small to read, while the sharper tiles read them. Each page and tile is read four times, as an image file is: upright with and without language correction, then turned a quarter each way. What Vision finds is placed on the 200 dpi page, and a mask is fitted to the ink as in the [image contract](image-component-contract.md).

OCR misreads tiny lettering differently in each drawing, so a label read wrongly once can be read rightly from the masked page. After the masks are planned, DocSan burns them into a copy of the page, reads that copy again at twice its size and masks any reference it still shows, before the owner confirms the page.

## Owner confirmation

Each page has its own plan: the proposed masks, the masks the owner adds and a digest of the file, the page and the masks. The owner confirms each page; release requires every page's exact digest. Any change, such as an added mask, a new profile term that adds a mask or a different file, asks again. Pages are drawn at 200 dots per inch (a page larger than 36 million pixels at that resolution is drawn smaller) and shown in the review as they will be exported.

## Emitted file

The output holds, in this order and nothing else:

1. the catalog, 2. the page tree, 3. one font: Helvetica, Windows-1252, every character half an em wide, not embedded, 4. the document information: `/Producer (DocSan)`,

then for each page a page object, its picture and its content stream; a cross-reference table and a trailer whose file identifier is the hash of the file before it.

Each picture is the page with the masks burned in, stored whichever way is smaller: as a JPEG (quality 0.85) holding only tables, one frame, its scans and the end marker, which suits photos and scans; or without loss as the compressed rows of an 8-bit PNG, grey when the page has no colour, which suits text. A dense A4 text page is about 250 KB stored without loss, a third of its JPEG size, and its text stays sharp for OCR and for the AI tool. Each content stream draws the picture over the whole page, then writes each word of the sanitized text where it sits on the page, on its line's baseline, in text rendering mode 3: invisible, but selectable, searchable and extractable. A word followed by another on its line ends with a space, so every reader finds the gap; a label sits over the name it replaces. The text layer holds the page's text with every match replaced by its label (people as "Elena M.", D-021) and every character under a mask left out, so it never holds what the pixels hide. Characters outside Windows-1252 lose their accents or become "?". Vector graphics, fonts, links, form fields, annotations, attached files, scripts and the original metadata are not carried (D-008).

## Verification

The verifier is independent of the sanitizer. It parses the output with its own grammar of what the writer emits and refuses anything else. That grammar covers every object, its order and its dictionary, the cross-reference table, the trailer with the file identifier and the content streams, which may only draw the page's picture over the page and write invisible text. It then:

| Check | What it reads |
|---|---|
| Structure | Every byte belongs to an object, the table or the trailer; a JPEG picture passes the JPEG checks of the image contract and is RGB; a stored PNG's rows decompress to exactly the picture's size, each with a known row filter |
| Metadata | The document information is exactly `/Producer (DocSan)`; anything else is a finding, and is scanned for names |
| Text layer | Every line and each page's lines joined (rejoining hyphenated words), through the verifier's own term and pattern rules |
| Pixels | Every page's picture enlarged to twice its size by macOS `sips` and read by Tesseract (sparse-text layout, English and French), which must report the enlarged size. Enlarged, Tesseract reads labels about eight pixels high that it misreads at the page's own size |
| Second extractor | PDFKit extracts each page's text from the file, which must have as many pages |

Before a clean result counts, the verifier plants a random name in a copy in three places: a text line, two lines that split it with a hyphen and the document information. Every view must find it, PDFKit included; Tesseract must read another random name drawn in a picture. Outcomes are kept apart: pass, findings and could not inspect. Only pass allows release.

## Private storage

Each PDF's drawn pages and the helper's readings are kept in the app's private folder while the batch uses them, so that checking the batch again after a decision does not draw and read every page again. They are deleted when a batch no longer holds the file.

## Known limits

Text OCR cannot read is covered only by the owner's confirmation of each page: a logo, decorative lettering and text at an angle other than a quarter turn. Labels under about eight pixels high on the page (about three points) are read by Vision on the sharper drawing but not by Tesseract, so for them the verifier is not a second check. A logo is masked by the owner (open choice O-007). The output's text layer keeps the words and their lines, not the layout of tables or columns. Pages are pictures, so the output is larger than a text PDF and its text cannot be edited. A name the text layer splits across two pages is not joined.


## Review workflow corrections (20 October 2026)

The page approval records its current file/page/mask digest without running OCR again. The app can approve a whole PDF after an explicit whole-document review; the approval is bound to the page-digest snapshot captured when that action opens. Export still reruns the engine, rejects changed plans and independently verifies the saved artifact. "Reviewed" is distinct from "Verified".

The area-mask editor accepts left, top, width and height as percentages of the current page. It clips masks to the image bounds and invalidates the approval when a mask changes. Pointer drawing remains available.

PDF planning masks long account-like IDs, explicit customer/name and postal-address fields, detected barcodes and small coloured marks next to company lettering in a page corner. Unrecognized logos and context still need review. The helper's local `regions` request accepts an image path and returns barcode bounds and connected colour-component bounds in upright image pixels, without decoded payloads; the engine makes the masking decisions. Helper-build hashes invalidate cached observations after updates. The independent verifier has its own identifier and numeric-context rules. All PDF person masks use full labels in the hidden text layer so masked first names do not remain searchable.
