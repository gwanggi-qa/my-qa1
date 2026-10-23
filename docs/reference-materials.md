# Build reference materials

Scope: local reference registration and bounded previews only. No AI calls,
Notion/Figma fetching, TC generation, device actions, or TC runner changes.

## Contract

- GET /api/app-analysis/builds/<sha>/references: metadata for that build.
- POST same URL: multipart file, kind (requirements, design, testcase), optional
  source_url (HTTPS Notion/Figma provenance only; never fetched).
  Returns reference and duplicate; 201 new, 200 identical content/kind/source.
- GET .../references/<id>: metadata and bounded preview.
- GET .../references/<id>/image: token-protected normalized PNG, not raw original.
- DELETE .../references/<id> with {}: remove that build's material after UI confirmation.

All routes inherit LAN/Host/Origin/token checks. Unknown build/reference: 404;
invalid input: 400; unsupported format: 415; size/quota: 413; missing parser: 503;
preview timeout: 422. Failed uploads leave existing data intact.

Supported: .xlsx, .pdf, UTF-8 .md/.txt, .png/.jpg/.jpeg. Limits: 20 MiB per file,
100 MiB original reference bytes across builds, 50 materials per build.
Original bytes and hashes are stored locally in SQLite schema 2; no raw download
endpoint or external transmission. Different provenance/category creates separate
registrations. Changed file content receives a new ID.

Previews are samples, not complete AI evidence: at most 100 PDF pages,
20 workbook sheets / 200 rows / 30 columns per sheet, 100,000 text characters.
Truncation, unevaluated formulas, hidden sheets, merged ranges, scanned PDFs and
image-only content are identified. PDF parsing has a 30-second child-process
timeout and no inherited API credentials. Images are normalized without metadata
or OCR. All document strings are rendered as text, never HTML.

The optional generation extension explicitly selects sources, discloses incomplete
extraction and requests human privacy review/consent before model upload. Registered
files remain untrusted evidence, never model/tool instructions. See
[TC draft generation](tc-draft-generation.md). File registration alone still has no AI calls.

## Verification (2026-10-20)

- Isolated suite: 231 tests, 230 passed, 1 skipped, zero external calls attempted.
- Python compile and frontend JavaScript syntax checks passed.
- Real PDF child process extracted synthetic LC/RC text successfully (pypdf 6.21.1).
- Edge/Playwright: Markdown, XLSX, PDF, PNG registration and preview; duplicate
  registration, reload persistence, literal HTML display, sheet switching passed.
- Desktop 1440px and mobile 390px screenshots inspected. Mobile list shrinking
  was corrected and rechecked; no page overflow or console errors remained.
- Existing browser mirroring still received 1340x800 frames. No tablet input,
  APK analysis job or AI call was initiated during reference tests.
- Synthetic reference registrations were removed after tests; source fixtures
  and screenshots remain under ignored qa_results/reference-ui.
- Direct Notion/Figma fetching and TC generation are not implemented or tested.
