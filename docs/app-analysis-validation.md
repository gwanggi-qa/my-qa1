# APK MVP verification - 2026-10-20 (Asia/Seoul)

## Confirmed

- REA 6.4.0 installed under qa_results/tools/rea.
- JADX 0.7.1 JAR SHA-256 matches the digest in the REA documentation.
- Project-local Java 21.0.8 selected; global Java settings unchanged.
- REA MCP initialization returned server name rea and version 6.4.0.
- Codex lists rea as enabled; the project config allows five read-only APK
  inspection tools and prompts before each call. Current conversations may
  require restart to refresh their tool catalog.
- Appium 3.1.1 / UiAutomator2 6.3.0 doctor: zero required fixes; optional
  bundletool, ffmpeg and GStreamer unavailable.
- Explicitly approved Appium session: 1340x800 window, UI source and screenshot
  retrieved. Session and temporary Appium server closed. No tap/swipe or target
  APK install was requested; Appium helper provisioning was permitted.
- Isolated regression: 219 tests, 218 passed, 1 skipped, 0 external operation
  attempts. No real device or network operation from this suite.
- Browser: Appium Settings sample APK registered and analyzed. Report:
  io.appium.settings 7.0.4 (145), 2858 classes, 118 resources, 19 components.
- Browser duplicate upload retained one build. Stop ended the analysis;
  another analysis subsequently completed.
- Browser page errors: none. Desktop 1440x1000 and mobile 390x844 reviewed.
  Mobile document width 390, no overflowing buttons.
- Existing workspace still received 1340x800 mirrored frames after restart.

## Evidence

Local, Git-excluded artifacts:

- qa_results/appium-environment-check.json
- qa_results/app-analysis-ui/desktop.png
- qa_results/app-analysis-ui/mobile.png
- qa_results/app-analysis-ui/existing-workspace.png
- qa_results/app_analysis/analysis.sqlite3

## Boundaries

This does not certify the user's target app, business requirements, full APK
semantics, signatures, generated TCs, Appium execution integration or iOS.
The website uses a fixed REA CLI command locally. Codex's read-only MCP
registration is separate; the existing automated planner still ignores user
configuration. No model was invoked for sample APK analysis.
In-app browser automation was unavailable due to a local runtime sandbox error;
visual/functional checks used a separate headless Edge/Playwright browser.
Existing tabs must refresh after server restart to receive a new page token.
