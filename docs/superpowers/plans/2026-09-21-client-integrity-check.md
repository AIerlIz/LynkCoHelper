# Client Integrity Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a release-bundle integrity check to the desktop client, then merge the client-only branch into `main`.

**Architecture:** The build script writes a SHA-256 resource manifest and embeds its digest in a compiled Python module. The launcher verifies both before starting the server or proxy. Source execution skips verification; packaged execution has no environment-variable bypass. This is not a digital signature and cannot prevent modification of the verifier or executable itself.

**Tech Stack:** Python 3.12, PyInstaller, `hashlib.sha256`, `unittest`.

## Global Constraints

- Keep cloud Worker code out of `LynkCoHelper`.
- Merge the client branch into repository `main`; remove the temporary branch after merge.
- Do not hard-code secrets or claim that the local check prevents reverse engineering.
- A failed release check must stop startup with a user-readable repair message.

### Task 1: Integrity module and tests

**Files:**
- Create: `desktop/integrity.py`
- Create: `tests/desktop/test_integrity.py`

**Interfaces:**
- `verify_release(root: Path) -> tuple[bool, str]` reads `integrity-manifest.json` below `root` and returns a stable error message on failure.
- `write_manifest(root: Path, output: Path) -> None` hashes the configured release files and writes deterministic JSON for the build script.

- [ ] Write tests for valid files, modified files, missing files, and absent manifests.
- [ ] Implement SHA-256 streaming hashes and deterministic manifest generation.
- [ ] Run `python3 -m unittest tests/desktop/test_integrity.py -v`.

### Task 2: Build and launcher integration

**Files:**
- Modify: `desktop/packaging/build.py`
- Modify: `desktop/launcher.py`
- Modify: `desktop/README.md`

**Interfaces:**
- The build script creates the manifest before PyInstaller and bundles it as `desktop/integrity-manifest.json`.
- The launcher calls `verify_release(package_root)` before importing or starting the local HTTP/proxy services.

- [x] Add a packaged-only check before both application and proxy entry points, with no environment-variable bypass.
- [ ] Add the manifest to the PyInstaller data files.
- [ ] Test launcher failure with a modified resource and document the limitation.

### Task 3: Verification and integration

**Files:**
- Modify: `docs/superpowers/plans/2026-09-21-client-integrity-check.md`

- [ ] Run integrity tests, desktop tests available in the environment, and `git diff --check`.
- [ ] Commit the client-only branch and push it to `LynkCoHelper`.
- [ ] Merge the branch into `main`, push `main`, and delete the temporary remote/local branch.
