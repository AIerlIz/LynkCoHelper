# Task 4 report: cloud timeout alignment and release verification

## Changes

- Cloud requests now use one deadline per operation: 35 seconds for normal requests and 120 seconds for the run-now request.
- Proxy and direct fallback receive only the remaining deadline. Response-body reads are bounded by that same deadline, so a stalled body cannot extend a request indefinitely.
- Browser requests use 45 seconds for normal operations and 130 seconds for run-now, leaving the backend time to return a structured result.
- Web operations track busy state per initiating button. Unrelated controls remain available while one action is running.
- Startup keeps the local status rendered before the cloud refresh. A refresh or polling failure leaves the existing dashboard visible and reports the error locally.
- README now documents verified resource reuse, temporary-resource cleanup, Windows DPI behavior, network fallback budgets, and validation boundaries.

## TDD evidence

The new timing/loading tests were run red before implementation:

- `test_proxy_and_direct_fallback_share_one_remaining_budget`
- `test_read_timeout_does_not_extend_the_shared_budget`
- `test_run_requests_get_the_longer_but_single_budget`
- `test_cloud_and_browser_deadlines_leave_backend_time_to_reply`
- `test_only_the_initiating_control_becomes_busy`

After implementation, all focused tests passed.

## Verification

- Focused cloud/UI tests: 36 passed.
- Full desktop suite: 126 passed.
- JavaScript syntax: `node --check desktop/web/app.js` passed.
- Local macOS arm64 package build: passed.
- Packaged smoke test: passed (`packaged app, static assets, authenticated API, graceful exit, embedded proxy and local CA`).
- `git diff --check`: passed.

Windows executable packaging and real Windows DPI/network behavior remain CI/physical-machine checks; this macOS host cannot execute those artifacts.

## Review follow-up

Review found that a controller operation could perform several individually bounded cloud requests while retaining the operation lock. The follow-up makes the controller create one 35-second absolute deadline for every normal composite operation. `refresh`, activation, settings, deletion, claim, and recovery pass that deadline to all follow-up requests, including schedule-window refreshes. CloudClient now shortens its per-request deadline to the caller deadline.

New deterministic tests prove that a timed-out refresh releases the operation lock and that the following run action can proceed. They also prove settings, activation, and deletion share the same deadline with their schedule-window follow-up requests.

Follow-up verification: focused binding/cloud/web tests and JavaScript syntax passed; full desktop suite passed with 129 tests; `git diff --check` passed.
