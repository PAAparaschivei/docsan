# DOCX component contract

**Every part and element of a Word package is read, removed or blocks release. Nothing passes by default.**

Last reviewed: 2026-10-10. Status: Milestone 2. Applies to `.docx`, `.docm`, `.dotx` and `.dotm` packages; output is always a fresh `.docx`.

The sanitizer rebuilds a new document from the content model below and never copies parts from the original. "Removed" therefore means the component is inspected and deliberately not carried into the output. "Blocks" means the file is not exported until a later milestone supports the component.

## Package parts

| Part | Rule |
|---|---|
| `[Content_Types].xml`, `_rels/.rels`, main document, its relationships | Read |
| `word/styles.xml`, `word/numbering.xml`, theme, font table, settings | Read to resolve formatting and lists (D-023); style names, theme and settings are not carried |
| `word/footnotes.xml`, `word/endnotes.xml` | Read; notes become a numbered Notes section at the end |
| Headers and footers a section uses, with their relationships | Read and kept, sanitized like the body; their pictures are removed whole as logos (D-025) |
| Headers and footers no section uses | Removed |
| Comments and their extension parts, `people.xml` | Removed |
| Document properties, custom properties, sensitivity-label metadata, thumbnails | Removed |
| `customXml/` data, web settings, glossary, printer settings | Removed |
| Macros, toolbar, ribbon and key customizations, add-in (web extension) metadata, editor data, embedded fonts | Removed |
| Images referenced by a picture in the document, a header or a footer | Read, then masked and written again as new PNG or JPEG parts, or removed whole as a logo ([image contract](image-component-contract.md)) |
| Chart parts and the text shapes drawn on a chart | Read into a table and notes; the parts are not carried |
| SmartArt data and layout parts | Read into a list; the parts are not carried |
| Other media, embeddings, ink and ActiveX parts | Removed as parts; a reference to them in the content follows the element rules below |
| Any other part | Blocks |

## Content elements

| Element | Rule |
|---|---|
| Paragraphs, runs, text, tabs, line, page and column breaks | Read |
| Formatting of paragraphs, runs, tables, rows and cells | Resolved from styles and kept through the allowlist below |
| Headings, title and list paragraphs | Read; headings map to neutral heading styles so navigation still works |
| Tables, rows and cells, including nested tables | Read, with merged cells, widths and row heights |
| Inserted text and moved-to text | Read (changes accepted) |
| Deleted text and moved-from text | Removed |
| Hidden text | Removed |
| Hyperlinks | Display text and its formatting read; target removed |
| Page number fields (PAGE, NUMPAGES, SECTIONPAGES) | Kept live, so every page shows its own number |
| Other fields | Displayed result read as text; instruction removed |
| Content controls, smart tags and custom XML wrappers | Content read; tags, aliases and bindings removed |
| A single text box | Kept as a text box, in its place, with its size, fill, outline and text |
| Groups of shapes, canvases and VML text boxes | Text read as paragraphs after the anchoring paragraph, under a line saying the shapes and connectors are not kept |
| Bookmarks, comment anchors, proofing marks, permissions | Removed |
| Symbol characters | Removed |
| Footnote and endnote references | Replaced by a `[n]` marker |
| Pictures (PNG, JPEG, and GIF, BMP or TIFF converted to PNG) | Masked under the [image contract](image-component-contract.md) and confirmed by the owner; written as a picture named "Picture N" with no name, description or link, in line or floating where it was. A logo is removed whole, leaving nothing: every picture in a header or footer, a picture that matches a logo the profile remembers or another removed picture, and any picture the owner removes (D-025). A crop is applied before reading, so the hidden part never reaches the output. With `--remove-images` replaced by `[IMAGE-REMOVED]` |
| Linked pictures (outside the file) | Replaced by `[IMAGE-REMOVED]` |
| EMF drawings, such as Visio diagrams pasted as pictures | Drawn by DocSan, then masked like pictures; the drawing's own text is matched exactly as well as through OCR. A drawing DocSan cannot draw completely blocks |
| WMF and SVG-only pictures | Block; with `--label-unreadable` or `--remove-images` replaced by `[IMAGE-REMOVED]` |
| Embedded objects with a preview picture (Visio, Excel and others) | The preview picture is masked like a picture; the object's data is removed |
| Charts | A line naming the chart and its type, a table of the stored values formatted as the chart shows them, and notes for what the table cannot show (decision D-022); sanitized like text; the embedded workbook is removed |
| SmartArt | A line naming the diagram's layout, then a nested list of its text, numbered for processes and cycles; sanitized like text |
| Embedded objects without a preview, equations, WordArt, ink and unrecognised drawings | Block; with `--label-unreadable` or `--remove-images` replaced by `[OBJECT-REMOVED]`, `[EQUATION-REMOVED]` or `[DRAWING-REMOVED]`. With `--remove-images`, charts and SmartArt become `[CHART-REMOVED]` and `[DIAGRAM-REMOVED]` |
| Imported chunks, sub-documents, ruby text | Block |
| Strict Open XML namespaces | Block |
| Any other element | Blocks |

## Formatting kept (D-023)

Word applies formatting in layers: document defaults, the table style (with its header row, last row, first and last columns, banded rows and columns and corner cells), numbering, the paragraph style, the character style, then direct formatting. The reader resolves every layer in that order and writes the result as direct formatting.

| Element | Properties kept |
|---|---|
| Run | Font (common fonts only, see below), size, bold, italic, underline style and colour, strikethrough, double strikethrough, capitals, small capitals, colour, highlight, shading, character spacing and scale, kerning, raised or lowered position, superscript and subscript, right-to-left |
| Paragraph | Alignment, indentation, spacing before, after and between lines, keep with next, keep lines together, page break before, widow control, borders, shading, tab stops with leaders, hyphenation, right-to-left, paragraph frames, the paragraph mark's formatting |
| List | The document's own numbering: number style, text such as "%1.%2", start, restart, alignment, indentation and the number's formatting; list instances keep their own counters |
| Table | Width, alignment, indentation, cell spacing, borders, shading, fixed or automatic layout, cell margins, column grid |
| Row and cell | Repeated header rows, rows that cannot split, row height, cells before and after; cell width, horizontal and vertical merges, borders, shading, margins, no wrap, text direction, vertical alignment |
| Section | Section break type, page size and orientation, margins, header and footer distance, columns, page numbering style and start, vertical alignment, different first page, odd and even headers |
| Settings | Default tab stop, odd and even headers, automatic hyphenation, mirror margins, compatibility mode |
| Floating picture or text box | Position and what it is relative to, distance from text, wrapping (none, square, top and bottom), in front of or behind text, stacking order |

Style names, aliases, the theme, colour and font names the theme supplies, and anything that could carry text are not carried: theme colours and fonts are resolved to their values first. A font outside the list in `engine/src/docsan/data/fonts.txt` becomes Calibri, Cambria or Consolas, by the kind the font table records, because a corporate font's name can name the company. Each original paragraph style becomes a neutral style (Style1, Style2, ...) so that spacing Word suppresses between paragraphs of one style still is. Text effects added in Word 2010 (glow, reflection, gradient text) are not kept; the plain colour stays.

## Pictures and diagrams: three choices

The default keeps every picture and diagram, with names masked; a visual DocSan cannot read blocks the file. `--label-unreadable` (in the app: "Keep them, label what DocSan can't read") keeps everything DocSan can read and replaces only the rest with a label, including a picture that cannot be decoded or a drawing that cannot be drawn completely. `--remove-images` ("Replace them all with labels") replaces every visual with a label.

## Package checks

The reader blocks a file when it is not a ZIP package, is encrypted, is truncated, holds duplicate entry names, declares a DTD, exceeds the size or compression-ratio limits, or leaves a field unclosed.

## Emitted package

The output holds content types, package relationships, the document and its relationships, styles, numbering and settings, then one part per header and footer (`word/header<n>.xml`, `word/footer<n>.xml`, with relationships when they hold pictures) and `word/media/image<n>.png` or `.jpg` for each picture.

The verifier pins the package relationships by hash and the styles part by the hash of its fixed start plus the neutral styles Style1..StyleN. It requires content types to match the parts present exactly and settings to have the writer's form. It checks that the document's relationships are styles, numbering, settings, one per header and footer, and its pictures, that a header's or footer's are only its pictures, and that every media part has exactly one relationship. In the document, headers, footers and numbering it allows only the elements the writer emits, and every attribute value must match its own pattern or word list: numbers, colours as six hex digits, fonts from the font list, field instructions limited to the page number fields, picture and text box names in the writer's own form. Text may appear only in text elements, in list number texts, which every scan reads, and in a floating drawing's position. Every media part gets the image checks: structure, transparent pixels and Tesseract.
