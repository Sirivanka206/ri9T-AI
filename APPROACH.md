# CT-200 extraction and traceability approach

## What the PDFs contain

Inspection was performed on both supplied PDFs before parser rules were written. `ct200_manual.pdf` has 6 pages; `ct200_manual_v2.pdf` has 7. PyMuPDF found selectable text on every page and no embedded raster images, so OCR was not needed for these particular files. A scanned-only page is not silently treated as parsed: the parser emits a `low_text_page` warning and notes that OCR is not configured.

Observed structural cases that drive the implementation and tests:

- The title is a three-line, large-font block. Major headings are generally 16.5 pt, subsections 12.9 pt, but `3.2 Cuff Inflation Sequence` is bold at 11 pt. A font-size-only heading rule would miss it.
- The numbered heading `2.1.1.1 Battery Life Under Typical Use` has no `2.1.1` parent. It is kept at level 4 and attached to the nearest existing numbered ancestor, `2.1`, with a `missing_heading_level` warning.
- On page 3, `3.4 Auto Shutoff` appears before `3.3 Result Display and Classification`. Source order is retained and an `out_of_order_heading` warning is emitted rather than silently sorting the content.
- “Error Codes” occurs under both `4.2` and `7.1`. The distinct section number and parent context preserve two separate nodes.
- The `2.1` specifications table and `4.2` error-code table have no reliable grid structure in the extracted text; cells are positioned in aligned columns. The parser reconstructs rows from page coordinates and has regression checks for the numeric range and error rows.
- Content crosses page boundaries. For example, v1 section `3.1` spans pages 2–3; in v2 its first sentence moved to page 3. The `8.1` regulatory text in v2 extends to page 7. The parser keeps the current section open across page boundaries and records the page list.
- In v2, `4.3 Alarm Thresholds` crosses from page 4 to page 5. The parser preserves both page segments under the same node.
- No figures or embedded images were found in either PDF. The code emits an image-preservation warning if that changes in a future input.
- Version 2 adds `5.3 Data Export` and E6, changes the battery estimate from 300 to approximately 250 cycles and the low-battery trigger from 15% to 10%, changes the cuff increment from 40 to 30 mmHg, and changes E3 auto-deflation from 2 seconds to 1.5 seconds. These edits exercise both modified and added status paths.

## Parsing method and hierarchy rules

PyMuPDF extracts selectable text with block, line, span, font, and coordinate information. This is preferable to OCR here: it preserves accurate text/layout without introducing OCR substitutions. Heading recognition combines a section-number pattern with bold font flags or a 12.5 pt size threshold. The observed 11 pt bold `3.2` heading verifies why the font flag matters. The title is selected from the first large-font blocks.

Nodes are created for the title and each detected numbered heading. The section-number depth determines the level. A child is attached to the nearest existing numbered prefix; absent intermediate headings are not fabricated. The parser emits warnings for missing levels, out-of-order sibling numbers, low-text pages, unclassified pre-heading text, or other detected unsupported cases. Body blocks continue under the current section until another heading is found, including across page breaks.

Tables are detected from their column headers and reconstructed from x-coordinate clusters. Specifications use two columns; error-code rows use three. Multi-line cells are joined until the next code. The table is stored as structured column/row JSON and a readable flattened form is included in node body text and its content hash. Ordinary paragraphs and detected numbered/bulleted blocks remain distinct content blocks. The whole extracted text is retained on each version for diagnostics.

The three explicit irregularity tests use the real PDFs:

1. Duplicate “Error Codes” sections remain distinct and keep parents `4` and `7`.
2. The missing `2.1.1` heading is exposed while `2.1.1.1` remains level 4; the small-font `3.2` and the `3.4`/`3.3` ordering anomaly are also asserted.
3. Page-spanning `3.1`/`8.1` content and both table layouts are checked against extracted values, including the v2 E6 row.

## Initial failure and how it was fixed

The first error-table column split treated x-coordinates below 100 pt as column one. Inspection of the PDF spans showed that the “Meaning” cell starts at about x=79 pt, so the parser put meanings into the code column and did not produce a valid three-column table. I printed the span positions, narrowed the first-column boundary to x<70 pt, and added row-level regression assertions for `E1`–`E6`. The table now retains the multi-line descriptions and behaviors under the correct columns.

Validation also compares v1 and v2 extracted section content and page lists. The v2 test catches content whose page placement changes, while hashes intentionally ignore page number and line wrapping.

## Data model and version matching

SQLAlchemy/SQLite stores:

- `Document` and immutable `DocumentVersion` records, including source-file SHA-256 and full extracted text.
- `SectionNode` rows with a version-specific UUID, stable logical ID when matched, parent ID, source order, heading, level, body/raw text, block structure, source pages, content hash, and match status.
- `VersionChange` rows for unchanged, modified, added, removed, and ambiguous cases.
- `Selection` and `SelectionItem` rows. Each item stores the exact version-specific node ID, logical ID, version ID, source hash, heading, and text snapshot.

The matcher uses a one-to-one exact key: matched parent logical ID + section number + NFKC/whitespace/case-normalized heading. Body text does not affect identity, so an edited requirement retains its logical ID and is flagged `modified`. If duplicate candidates make the key non-unique, matching is withheld and `ambiguous` is recorded; the system does not choose the first title match. Re-ingesting identical PDF bytes for a document is idempotent and returns the existing version instead of creating a duplicate.

This deliberately conservative matcher can fail to recognize a semantically identical section after renumbering, retitling, or moving it to a different parent; it will be reported as added/removed rather than guessed. Conversely, an exact path/title match may keep an identity even if its meaning changed; the changed body hash then flags it for review. It is not a semantic similarity or requirement-equivalence engine.

## LLM output and generation policy

The Gemini prompt treats the manual as untrusted source data, prohibits invented thresholds/error codes/timings, asks for concrete steps and expected results, requires exact source node/version/hash references, and requests unsupported details explicitly. Pydantic validates a strict 3–5 case JSON schema; the server also rejects citations not present in the selection. `human_qa_review_required` must be true. Structured JSON mode is requested from Gemini, but provider output is still parsed and validated.

There is one initial attempt plus at most `LLM_MAX_RETRIES` attempts. A malformed or incomplete response is not stored as a successful generation; after retries, the API returns HTTP 502 with validation diagnostics. Missing credentials return HTTP 503. A successful generation is stored in a separate JSON file and includes the exact selection source snapshots and raw model response.

The duplicate-request policy reuses the most recent successful record matching selection ID, model, and prompt version. `"regenerate": true` explicitly creates a new immutable generation. The JSON store uses atomic file replacement, but it is a local single-process assignment store; concurrent multi-worker deduplication and production durability would require a transactional document database or locking.

## Staleness and impact

At retrieval time, each generated case's source references are resolved by logical ID in the latest version. The normalized hash includes heading and body, collapses whitespace, and applies Unicode NFKC. A difference is reported `stale`; no current match is `removed_requirement`; non-unique matching is `ambiguous_mapping`; equal hashes are `current`. A unified text diff and source/current version numbers are returned. Whitespace-only and ligature-normalization differences do not create false changes.

A hash only establishes textual equality/inequality. It cannot decide whether a wording change is harmless or whether a changed pressure/timing threshold invalidates a test. Any changed source is conservatively stale, and every case remains marked for human QA review, including current cases. There is no automatic regeneration.

## Deviation from the suggested stack

The required Python, FastAPI, Pydantic, SQLAlchemy, and SQLite stack is used. Generated outputs use an atomic local JSON document store instead of MongoDB because the assignment data and expected run are single-user/local and a JSON file preserves immutable generation/source snapshots with no additional service. This is not a production multi-user or distributed store.

## Decision log

1. **Most likely silent wrong result:** a PDF layout/cell-ordering error can place a numeric threshold under the wrong table field while still yielding valid JSON. I catch this with coordinate inspection, structured table-row assertions against both PDFs, parser warnings, and visual/manual review of the source pages.
2. **Simplicity over correctness:** version matching is exact path-based rather than semantic/fuzzy. Renumbered or moved-but-equivalent requirements can first show up as removed plus added; duplicate-path cases become ambiguous. Production use would need reviewed match suggestions and explicit human adjudication, not more aggressive automatic matching.
3. **Unsupported input:** an encrypted PDF is rejected with a clear parse error; a scanned-only page is not OCRed. Low selectable-text pages receive an explicit warning, so the caller can supply OCR-processed text or add/configure an OCR engine rather than trusting incomplete output.

## What I would improve with more time

- Add a configurable OCR fallback with confidence scores and image-to-text evidence for pages with missing selectable text.
- Add golden page render comparisons and OCR/table verification for image-based or gridless tables beyond the observed patterns.
- Add reviewed fuzzy-match suggestions, an audit trail for human decisions, and targeted semantic impact analysis for changed safety values.
- Move generated outputs to MongoDB or another transactional store for concurrent workers, retention controls, and stronger durability.
- Add authentication, rate limits, request-size controls beyond the current upload cap, and operational logging/metrics before deployment.
