# Browser E2E for br-afisha-e2e

Run these commands from the repository root in macOS Terminal. The Docker Compose project is explicitly isolated as `br-afisha-e2e`; these tests never contact the production project.

```sh
docker compose -p br-afisha-e2e --env-file .env.e2e -f docker-compose.yml -f compose.e2e.yml up -d --build
cd frontend/e2e
npm install
npx playwright install chromium
set -a
source ../../.env.e2e
set +a
export E2E_ADMIN_USERNAME="$ADMIN_USERNAME"
export E2E_ADMIN_PASSWORD="$ADMIN_PASSWORD"
npm test
```

Use the existing local `.env.e2e` credentials; do not paste them into test files or commit that file. The test generates unique event titles, submits one event using `/submit-event`, and creates a draft from `/admin`. The generated event records remain in the isolated E2E database for audit/repeatability; the test does not delete records or volumes. Run against the same `br-afisha-e2e` project each time.

Playwright screenshots, trace archives, and video for failed tests are written under `frontend/e2e/test-results/`; the HTML report is in `frontend/e2e/playwright-report/` (`npx playwright show-report playwright-report`).

This suite uses the real frontend and admin UI. It does not install Playwright in either Docker image. If frontend application source changes, rebuild only the isolated frontend service before running:

```sh
docker compose -p br-afisha-e2e --env-file .env.e2e -f docker-compose.yml -f compose.e2e.yml build frontend
docker compose -p br-afisha-e2e --env-file .env.e2e -f docker-compose.yml -f compose.e2e.yml up -d frontend
```

Changes to frontend application source require a rebuilt frontend image. The runtime-error and direct SSR tests cover the event-detail failure.

If React #441 recurs, inspect the frontend server log for the original exception and digest printed during the event-page request:

```sh
docker compose -p br-afisha-e2e --env-file .env.e2e -f docker-compose.yml -f compose.e2e.yml logs --tail=200 frontend
```

Before the fix, the failing event-page RSC response contained digest `3701493170`. The browser production bundle omitted the original server exception.

## Scenario status

The read-only runtime-error and direct SSR tests passed against the isolated Docker frontend after rebuilding it. The full lifecycle scenario was not run in this check.

- **NOT RUN (prepared, runtime unverified):** homepage and event cards; title search; detail page; pending submission hidden until moderation; publication via admin; editing and cancellation via admin; cancelled and draft events absent from public results; empty result; UI-driven city/category/date filters and reset; admin login/session and CSRF header on a UI write.
- **Conditional:** pagination is exercised when the isolated catalog has multiple pages; otherwise the test currently passes without exercising a next-page link.
- **GAP:** network-error rendering is not exercised. Event results are loaded during server rendering, so browser request interception cannot simulate a backend failure for that server-side request. It requires a controlled API outage in the isolated E2E stack or an application-level injectable API endpoint.
- **SKIP:** none by default. The single scenario is skipped if either E2E admin credential variable is absent.
