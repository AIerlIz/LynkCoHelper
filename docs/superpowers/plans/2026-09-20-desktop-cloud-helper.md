# Desktop and Cloud Helper Implementation Plan

> **For agentic workers:** Use subagent-driven-development for the bounded cloud implementation and code review; integrate and verify each component before marking it complete.

**Goal:** Deliver a runnable desktop binding assistant and deployable free-tier cloud service matching the approved design.

**Architecture:** A packaged Python launcher serves a local browser UI, manages mitmproxy and OS credential storage, and calls a TypeScript Worker. The Worker owns encrypted account credentials, D1 persistence, invitation/management authentication, and durable daily execution. No application secrets or real account fixtures enter source control.

**Tech Stack:** Python 3.12, mitmproxy, keyring, qrcode, PyInstaller; TypeScript, Cloudflare Workers/D1, Web Crypto; local HTML/CSS/JavaScript and Lucide icons.

## Global Constraints

- Approved specification: `docs/superpowers/specs/2026-09-20-lynkco-desktop-cloud-design.md`.
- Same mobile/cloud session refresh coexistence and iPhone trusted-certificate capture are confirmed premises.
- Preserve the existing `LynkCoHelper` CLI. Add desktop/cloud components without unrelated refactoring.
- Never log secrets, store raw captures, submit live fixtures, or put application signing secrets in the desktop bundle.
- Bind only complete credentials; local completeness checking precedes explicit user-triggered cloud validation and activation.
- Local management API is loopback-only and authenticated. Proxy and certificate landing routes are a separate LAN surface.
- A public cloud deployment is not permission to upload existing personal account credentials without the planned user confirmation.
- Tests precede behavioral implementation. Use deterministic test fixtures for upstream effects; do not repeatedly execute real sign/share actions for testing.
- Cloud is capped at 20 owners, one binding per owner; Cloudflare Free limits must not cause an automatic paid upgrade.

## Shared Interfaces

Cloud response envelope:

```typescript
type Result<T> = { ok: true; data: T } | { ok: false; error: { code: string; message: string } };
interface CapturedSession {
  token: string;
  refreshToken: string;
  deviceId: string;
  platform: 'IOS' | 'ANDROID';
  appVersion?: string;
  appBuild?: string;
  glDevId?: string;
  deviceImei?: string;
}
interface Binding {
  id: string;
  label: string;
  status: 'active' | 'paused' | 'needs_rebind' | 'service_error';
  scheduleTime: string;
  doShare: boolean;
  canShare: boolean;
  nextRunAt: number;
}
interface Run {
  id: string;
  businessDate: string;
  status: string;
  startedAt: number | null;
  finishedAt: number | null;
  pointsBefore: string | null;
  pointsAfter: string | null;
  signStatus: string;
  shareStatus: string;
  errorCode: string | null;
  message: string | null;
}
```

Timestamps are Unix milliseconds. Request authorization is `Authorization: Bearer <managementToken>`. Mutation retries reuse `Idempotency-Key`. Cloud IDs are opaque UUIDs, not mobile numbers or tokens.

| Route | Request | Success data |
| --- | --- | --- |
| `GET /health` | none | `{service: 'lynkco-helper', configured: boolean}` |
| `POST /v1/admin/invitations` | Admin bearer secret, `{}` | `{inviteCode, expiresAt}` |
| `POST /v1/owners` | `{inviteCode}` | `{ownerId, managementToken, recoveryCode}` |
| `POST /v1/owners/recover` | `{recoveryCode}` | `{ownerId, managementToken, recoveryCode}` |
| `POST /v1/binding-candidates` | `{session: CapturedSession}` | `{id, expiresAt, preview: {points: string, alreadySigned: boolean}, capabilities: {share: boolean}}` |
| `POST /v1/binding-candidates/{id}/activate` | `{label, scheduleTime: '08:10', doShare: false}` | `Binding` |
| `GET /v1/binding` | none | `Binding \| null` |
| `PATCH /v1/binding` | partial `{label, status, scheduleTime, doShare}` | `Binding` |
| `DELETE /v1/binding` | none | `{deleted: true}` |
| `POST /v1/binding/runs` | `{}` | `{id, status}` |
| `GET /v1/binding/runs` | optional `cursor` | `{items: Run[], nextCursor: string \| null}` |

Cloud environment contract: `DB` (D1), `LYNKCO_APP_SECRETS` (JSON matching current application configuration), `CREDENTIAL_KEY` (base64 32-byte AES key), `ADMIN_KEY` (random admin bearer secret). Optional test upstream injection belongs to test harness dependency injection, never request-controlled URLs.

Desktop normalized public state exposes no captured/session/management credentials. The local UI receives one temporary local API token in a URL fragment, removes it from the address bar, and sends it only to local `/api/` endpoints.

## Task 1: Cloud Service and Protocol Adapter

**Owner:** bounded cloud implementation agent. Own `cloud/` only.

**Files:** `cloud/package.json`, `cloud/tsconfig.json`, `cloud/wrangler.jsonc`, `cloud/migrations/0001_initial.sql`, `cloud/src/{index,auth,bindings,store,crypto,runner}.ts`, `cloud/src/lynkco/`, `cloud/tests/`.

**Interfaces:** Implements the shared API above. Port request signatures using existing Python behavior and independent test vectors. Use native crypto for HMAC/AES and a maintained dependency for MD5. Read the existing sign/share/login code before implementation.

- [ ] Add executable tests for missing/invalid management credentials, cross-owner candidate access, incomplete credentials, duplicate invitation redemption, recovery rotation, candidate expiry, same-day run deduplication, stale credential writes, and invalid upstream business responses. Run and observe the expected failures.
- [ ] Implement migration, AES-GCM with owner/record AAD, invitation/owner recovery, hashed bearer tokens, bounded expiring idempotency responses, candidate validation and activation, settings and deletion.
- [ ] Implement the protocol adapter, preserve token/device pairing, and make cloud execution independent of Python/local `env.json`.
- [ ] Implement one due binding per minute, 180-second lease, 120-second run deadline, persisted stages, bounded retries and no blind replay of unknown share writes. Keep `needs_rebind`, configuration failures and network failures distinct.
- [ ] Test real D1 semantics with local Workers/Miniflare tooling, type-check, and produce a deployable dry-run bundle. Report commands and outcomes.
- [ ] Document maintenance commands for migrations, secrets, invitations and deployment; include free-tier measurement limitations.

Required example behavior:

```typescript
// A successful HTTP response carrying a failed business code must never create a candidate.
assert.equal(response.status, 422);
assert.equal((await response.json()).error.code, 'CREDENTIAL_INVALID');
// A second management identity must not activate another owner's valid candidate.
assert.equal(otherOwnerActivation.status, 404);
```

## Task 2: Desktop Capture and Local API

**Owner:** primary agent. Own `desktop/` and `tests/desktop/`.

**Files:** `desktop/{capture,binding,proxy,credential_store,cloud_client,local_api,launcher}.py`, `desktop/capture_addon.py`, `desktop/requirements.txt`, `tests/desktop/`.

**Interfaces:** A capture parser consumes a URL, header mapping, status and JSON response and returns `CapturedSession` or no complete candidate. The mitmproxy plugin posts the result over an authenticated loopback-only callback. The controller publishes only sanitized progress/preview; upload is a separate explicit operation.

- [ ] Write tests showing ordinary token-only traffic is incomplete, failing login responses are rejected, returned refreshToken wins over request refreshToken, device identifiers come from the same flow, and unrelated domains cannot inject candidates. Run failing tests.
- [ ] Implement structured capture parser and state transitions; never parse/store password or verification-code fields.
- [ ] Add local API integration tests using a real HTTP server: absent token, hostile Origin/Host, path traversal, unauthenticated capture callbacks and full-token exposure must fail.
- [ ] Implement API, secure keyring storage, cloud envelope client, LAN certificate surface and paired-phone proxy lifecycle. Do not fall back to plaintext management credentials.
- [ ] Test subprocess stop/cleanup behavior and ensure binding success retains proxy pass-through until the user acknowledges removing the mobile proxy.

Parser example:

```python
session = parse_session(
    'https://app-services.lynkco.com.cn/auth/login/refresh?deviceId=phone-a&refreshToken=old',
    {'publicplatform': 'iOS'},
    200,
    {'code': 'success', 'data': {'centerTokenDto': {'token': 'new-token', 'refreshToken': 'new-refresh'}}},
)
assert session['deviceId'] == 'phone-a'
assert session['refreshToken'] == 'new-refresh'
```

## Task 3: Browser Interface and Integration

**Owner:** primary agent; cloud implementation remains isolated in `cloud/`.

**Files:** `desktop/web/{index.html,app.js,style.css}`, bundled icons/assets, UI browser tests.

- [ ] Build an operational Chinese interface: invitation/recovery, account overview, binding wizard, history, settings and proxy-cleanup confirmation.
- [ ] Wire every visible control to local API. Surface loading, empty, error, expired candidate and disconnected-cloud states without invented success records.
- [ ] Show proxy address, port, local certificate QR code and platform-specific setup steps; do not imply QR changes phone settings automatically.
- [ ] Test browser workflows against controlled cloud/capture responses, including expired candidate and failed activation. Use real rendering and desktop/mobile screenshots.
- [ ] Verify no horizontal overflow, overlapping controls, secret-bearing URLs/logs or nonfunctional buttons.

## Task 4: Packaging, Deployment and Verification

**Owner:** primary agent, followed by independent review.

**Files:** `desktop/packaging/`, `desktop/README.md`, root README entry, execution report under `docs/superpowers/`.

- [ ] Add reproducible PyInstaller builds bundling static assets, proxy entrypoint and required dependencies. Keep runtime state outside installation/extraction directories.
- [ ] Build and smoke-test the current macOS artifact. Add Windows/macOS build configuration; mark unavailable platform runs as not verified rather than passed.
- [ ] Deploy new isolated Worker/D1 resources through existing authenticated Cloudflare tooling without changing billing plans or overwriting unrelated deployments.
- [ ] Upload only application-level signing configuration for service setup. Exercise cloud provisioning and sanitized fixtures; request user interaction for real-account capture/activation as required by the product flow.
- [ ] Run Python tests, TypeScript tests/type-checks, migration/deploy smoke checks and browser screenshot checks. Review changes, fix material findings, record remaining live-device/free-tier measurements.
- [ ] Provide the local assistant URL/artifact and service status, distinguish implementation completion from unperformed real-phone and seven-day acceptance.

## Progress

- Worktree and approved design baseline: complete.
- Task 1: implemented; 35 D1/protocol tests pass, independent review issue fixed.
- Task 2: implemented; 26 desktop/capture/API/proxy tests pass.
- Task 3: implemented; desktop/mobile browser fixture workflows and error states verified.
- Task 4: macOS ARM64 build and packaged smoke test pass; isolated cloud deployed. Default-domain reachability, real phones, other platforms, free-tier measurements and seven-day observation remain open. See `../2026-09-20-desktop-cloud-test-report.md`.
