# Security Policy

## Reporting a vulnerability

If you find a security vulnerability in LensLedger, **please report it privately** — do not open a public issue, since a public issue tips off potential attackers before a fix is out.

Use GitHub's **private vulnerability reporting**: go to the
[Security tab](https://github.com/WeirDave/LensLedger/security)
and click **Report a vulnerability**. This opens a private channel visible only to the maintainer.

Please include:

- What the vulnerability is and where it lives (file / feature / version).
- Steps to reproduce, or a minimal proof of concept.
- The version shown in the app header (e.g. `v0.41.1`).

You'll get an acknowledgment as soon as it's seen. Confirmed issues are patched on a priority basis and credited in the release notes unless you ask otherwise.

## Supported versions

LensLedger ships continuously; only the **latest released version** is supported for security fixes. The current version is shown in `src/product.py` (`APP_VERSION`) and on GitHub Releases. If you're running an older build, update before reporting.

## Scope and design notes

LensLedger is a **local-first desktop application** that runs a local HTTP server to serve its UI:

- There is no LensLedger account, cloud database, or telemetry backend. Your photos, metadata, and SQLite database stay on your machine. The local server binds to `localhost` and is not exposed to the network.
- Because LensLedger reads and indexes photos from your filesystem, the most serious class of vulnerability is **path traversal** — anything that could trick the server into reading, writing, or deleting files outside the configured library paths. Reports of traversal vectors are especially valued.
- LensLedger shells out to a bundled ExifTool binary to extract photo metadata. Inputs to ExifTool are file paths from the scanned library; any injection vector that could influence those paths or arguments is in scope.
- The "Trash" feature moves files to a review bin folder and the "Permanent delete" / "Empty trash" actions delete files from disk. These operations are guarded by confirmation prompts in the UI but are irreversible once confirmed.

## Dependency tracking

LensLedger's Python dependencies are declared in `requirements.txt`. Front-end assets (JS, CSS) are served from `web/` with no build step.
