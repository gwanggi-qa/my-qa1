# Figma import and AI TC drafts

## Scope and contract

No APK/device installation, TC execution, or automatic approval. Notion continues
to use file exports. Figma REST reads only one explicitly linked frame using the
server's FIGMA_ACCESS_TOKEN with file_content:read permission. No key is accepted
from or returned to browsers. Figma MCP in Codex is not the web server credential.

- GET /api/app-analysis/integrations: Figma configuration and AI provider status.
- POST /builds/<sha>/figma (under /api/app-analysis): {url}; linked node required.
  Import normalized node metadata and optional PNG as a design reference snapshot.
- POST /builds/<sha>/draft-context: {reference_ids, scope, count, include_images}.
  Return redacted bounded context, context_hash, provider, warnings and image IDs.
- POST /builds/<sha>/drafts: same fields plus context_hash and consent:true.
  Snapshot context and queue one AI request; 202, global busy 409, no fallback.
- GET /builds/<sha>/drafts and /drafts/<id>: list/detail persistent batches.
- POST /builds/<sha>/drafts/<id>/stop: {}; stop generation, discard late results.
- PATCH /builds/<sha>/drafts/<id>/cases/<case_id>: editable fields, review status,
  revision. Optimistic concurrency 409; AI never sets approval or execution state.
- GET /builds/<sha>/drafts/<id>/export: approved cases only as XLSX. No direct run.
- GET /api/app-analysis/testcase-template: blank standard TestCase workbook.

Limits: 1-20 draft cases, 1-8 explicitly selected references, max 4 images, bounded
source excerpts with omission warnings. Preview is mandatory; the hash covers
provider identity, source excerpts, image digests, scope and requested count.
Known credentials/emails are redacted in text. Images need explicit inclusion and
human privacy review; automated redaction is not complete privacy protection.

SQLite schema 3 adds generation jobs with immutable source snapshots, original
model response/usage, editable case copies and revision. On restart unfinished
jobs are interrupted, never resumed. Review approval is not a test Pass and is
not an execution approval. No existing TC state is overwritten.

Expected-result authority: requirements/design establish intended behavior;
existing TC provides style/scenarios; APK is implementation evidence only.
Every generated case cites supplied evidence IDs; absent sources, conflicting
requirements, unsupported assumptions or missing data remain review questions.
Only structural validity and source identity are machine-checked, not truth of
the model's interpretation. Original and edited cases remain distinguishable.

## Primary references

- https://developers.figma.com/docs/rest-api/file-endpoints/
- https://developers.figma.com/docs/rest-api/authentication/
- https://developers.figma.com/docs/rest-api/scopes/
- https://learn.chatgpt.com/docs/non-interactive-mode

Figma errors distinguish missing token, authentication/permissions, missing node,
rate limit, timeout and invalid response. API requests use a fixed HTTPS host;
render downloads use explicit Figma CDN hosts, no redirects or auth forwarding.
AI uses existing Codex JSON executor by default; paid API only when explicitly
configured as provider. CLI errors do not trigger API calls or invented drafts.

## Setup

Set FIGMA_ACCESS_TOKEN in the Windows account/process environment that starts
mirror_server.py, then restart the server while idle. Do not paste it in browser
forms, logs, chat or committed files. Status exposes only whether it is configured;
file permission is established on the actual import request. A frame link must
include node-id. Notion direct import is not implemented; use PDF/Markdown exports.

AI generation reuses AI_PROVIDER and the existing Codex ChatGPT login. Export uses
the fixed 14-column TestCase contract in tc_format.py, based on testcase.xlsx:
통합ID, 제목, 우선순위, 1Depth, 2Depth, 3Depth, Type, 테스트목적, 사전조건,
테스트절차, 기대결과, 결과, 자동화 결과, 비고. Unknown hierarchy stays blank;
Type describes QA classification, distinct from the scenario category. Both
result columns start empty. Review approval must never be exported as PASS.
Existing approved drafts remain exportable; missing hierarchy/type is not invented.
Reviewers can edit all hierarchy/type fields before approving an updated draft.
The Sources evidence sheet remains separate. No private template data is embedded
in code or added to AI input by the standardization change.
Review approval is recorded without a named authenticated reviewer because the
existing BTS has shared LAN access, not per-user login. Textual source snapshots
and image hashes are retained even if the reference entry is removed later; image
pixels remain in the reference entry, so keep it for later visual review.
Automatic duplicate elimination is
limited to identical cases within one generated batch; cross-batch semantic duplicate
detection and automatic TC registration/execution are outside this phase.

## Verification (2026-10-20)

### Draft wait and diagnostics update

- New TC draft jobs use a 600-second provider wait. Existing Random/TC CLI
  requests keep 120 seconds; existing API requests keep 45 seconds.
- The UI restores elapsed time from the saved job start time and updates every
  second. Completion freezes total time. Polling failures show a delayed-status
  notice, not a false generation failure. Stop remains distinct from failure.
- Metrics retain timeout/elapsed seconds and safe error codes: timeout,
  authentication, usage_limit, connection, invalid_response, cli_execution, unknown.
  Existing generic failures are not reclassified retrospectively. Missing usage
  is shown as unavailable, never zero. No raw provider output or credentials are saved.
- Explicit API mode has a bounded wait and discards late responses on Stop/timeout;
  an already sent remote request cannot be cancelled or its billing undone by
  abandoning the local wait. Codex mode never falls back to the paid API.
- No automatic retries, production reference retransmission, or generation-count
  changes are part of this update.
- Update verification: 259 isolated tests, 258 passed and one existing skip;
  zero external-operation attempts. Mock-clock checks cover continued waiting
  past 120 seconds, rejection at/after 600 seconds, and late API response discard.
  Mocked Edge checks cover restored timers, delayed polling, Stop, terminal-time
  freezing, categorized errors, desktop/mobile layout and the original
  generate/review/approve/export workflow. No live AI request was made.

- Isolated suite: 249 tests, 248 passed and one existing conditional skip;
  external-operation attempts: zero.
- Real Codex generation from synthetic Markdown and a synthetic design image:
  one request, 11,721 input tokens, 400 output tokens (reported by CLI).
  It produced one unexecuted draft, then browser review/edit/approval and XLSX
  export succeeded. No company document or device input was used in this check.
- Figma requests were mocked in both API and browser integration tests. Missing
  token, authentication, permissions, missing node, rate limit, redirect refusal,
  host allowlist, PNG omission and source persistence were checked. Actual private
  Figma retrieval is pending a real frame link and FIGMA_ACCESS_TOKEN.
- Wrong evidence IDs, malformed AI output, no consent, stale context/revision,
  missing AI provider, stop with late results, restart, image inclusion and no
  paid-API fallback in Codex mode were verified.
- Edge desktop (1440px) and mobile (390px) upload/import/generate/review/export
  screens were inspected. No page overflow or JavaScript errors remained.
- The operating server was updated after preserving idle state and backing up
  SQLite. Existing reference uploads and 1340x800 mirror frames passed regression.
- No code commit/push, device automation, or production AI generation was performed.
