# APK Analysis MVP

Scope: standalone APK registration, SHA-256 deduplication and static REA/JADX
declarations only. No APK installation, AI calls or test execution from this UI.
Completion is not a QA Pass. Existing runners and uploads remain unchanged.

## API contract

Existing network, Host, Origin and page-token guards apply to every API.

| Method | Path | Result |
| --- | --- | --- |
| GET | /app-analysis | Page and token bootstrap |
| GET | /api/app-analysis/environment | Tool inventory, no device input |
| GET/POST | /api/app-analysis/builds | List / multipart file upload, .apk, max 200 MiB |
| GET | /api/app-analysis/builds/<sha256> | Build and latest job evidence |
| POST | /api/app-analysis/builds/<sha256>/analyze | Empty JSON, 202 job, 409 busy, 503 missing tools |
| POST | /api/app-analysis/jobs/<job_id>/stop | Empty JSON, stop owned process |

Upload returns 201 or 200 duplicate. Errors: 400 invalid APK, 413 oversized,
404 unknown ID. One worker; states running/completed/failed/stopped/interrupted.
Restart marks unfinished jobs interrupted; never retries automatically.
SQLite schema v1 and APKs: ignored qa_results/app_analysis. Back up separately.
builds stores hash, filename, bytes, timestamp; jobs stores FK, state, time,
sanitized evidence and error code. No raw APK download API.

## Safety

No command/path accepted from HTTP. ZIP limits: 20,000 entries, 1 GiB expanded,
no encryption, unsafe/duplicate names or nested APKs; root manifest required.
Registered APK storage is limited to 2 GiB. Only one BTS server process may use
the analysis database at a time. Do not run multiple Flask workers against it.
Split declarations rejected after decoding. REA 6.4.0, hash-verified JADX 0.7.1.
Timeout 180 seconds, 16 MiB output, 2 GiB Java heap, two active processors.
Only package/version/SDK, permission/component names and counts are retained.
Raw manifest/code/stderr and credentials are not exposed to UI or model.
Runtime behavior, business correctness and signature authenticity are unverified.
No RBAC yet: confidential build metadata is shared with the trusted LAN.

## Setup

python tools/setup_analysis.py installs pinned local tools, not device apps.
QA_ANALYSIS_JAVA_HOME and QA_ANALYSIS_NODE override local tool selection.
Appium session checks require explicit consent for helper app provisioning.
TC generation, approval identities and dynamic exploration are later phases.

The local .codex/config.toml registers REA with only five read-only Android
inspection tools and per-call approval. Restart the Codex session to load it.
Existing automated Codex planning uses --ignore-user-config in a temporary
directory; it does not inherit this REA connection. The web MVP calls the same
REA analysis workflow through its fixed local CLI, not through the model.
