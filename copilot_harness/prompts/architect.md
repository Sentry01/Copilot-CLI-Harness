# Task: define the contract and scaffold a runnable app skeleton

Inputs: `harness/PRD.md`$change_note and `harness/requirements.json`.

Tests will be written **before** the implementation, against the contract you define here.
Every route, endpoint, accessible name and `data-testid` a test needs must therefore be
specified now, precisely.

## 1. `harness/contract.json`

```json
{
  "version": 1,
  "api": [
    {"method": "POST", "path": "/api/auth/register", "summary": "Create an account", "auth": "none",
     "request": {"email": "string", "password": "string (min 8)"},
     "responses": {"201": {"id": "string", "email": "string"}, "409": {"error": "Email already registered"}, "422": {"error": "string", "fields": "object"}}}
  ],
  "ui": {
    "routes": [{"path": "/register", "name": "Register", "purpose": "Sign-up form", "auth": "none"}],
    "testids": {"register-form": "The sign-up <form>", "flash-message": "Status message region (role=status)"},
    "accessible_names": {"/register": ["textbox 'Email'", "textbox 'Password'", "button 'Create account'", "heading 'Create your account'"]},
    "messages": {"email_taken": "Email already registered"}
  },
  "test_hooks": {
    "reset": "POST /__test__/reset",
    "seed_users": [{"email": "admin@example.test", "password": "Admin-Passw0rd!", "role": "admin"}]
  }
}
```

- Use semantic HTML and accessible names first (tests prefer `getByRole` / `getByLabel`).
  Use `testids` only where no stable accessible name exists.
- Record exact user-facing messages that tests will assert on.
- `test_hooks.reset` is `"METHOD /path"` on the app itself (no host, no `//`). It must restore
  a clean, seeded state, answer 2xx without redirecting, and may only be enabled when
  `NODE_ENV=test` or `APP_ENV=test` (404 otherwise). The acceptance fixtures call it before
  every test.
- Errors are JSON `{"error": "..."}` with correct status codes (400/401/403/404/409/422/429).

## 2. `harness/app-contract.json`

```json
{
  "stack": "Node 22 + Express 5 + SQLite (better-sqlite3), server-rendered HTML",
  "install": ["npm --prefix app ci"],
  "build": ["npm --prefix app run build"],
  "start": "npm --prefix app start",
  "base_url": "http://127.0.0.1:3000",
  "health_path": "/health",
  "env": {},
  "startup_timeout_seconds": 120
}
```

- Commands run from the project root with `sh -c`.
- The server MUST listen on `process.env.PORT` and `process.env.HOST` when set. The harness runs
  the suite on a random free port.
- `start` must serve a production build (not a watch-mode dev server).
- Use the stack the PRD asks for. Otherwise choose a mainstream, well-documented stack that
  runs with Node 22 or Python 3.12 and needs no external services. Prefer SQLite and file
  storage over databases that must be installed separately.
- Serve every asset (scripts, styles, fonts, images) from the app itself. The acceptance runner
  blocks all hosts except the app, so CDN or third-party resources fail to load in tests.
- Use `npm ci` only if you generate `app/package-lock.json` (run `npm install` once in `app/`).
- Pin dependencies in a lockfile CI can audit: `app/package-lock.json` (npm) or
  `app/pnpm-lock.yaml` for Node; a pinned `app/requirements.txt` or `app/uv.lock` for Python.
  The dependency audit fails on other layouts.

## 3. Scaffold `app/`

A minimal skeleton that installs, builds and starts. It must contain:
- `GET <health_path>` → 200 `{"status":"ok"}`.
- The reset hook from `test_hooks`, active only in test mode.
- Baseline security middleware: security headers, no `X-Powered-By`, JSON error handler without
  stack traces.
- An `app/README.md` describing structure and how to run.
- **No product features.** Every acceptance test should fail against the skeleton, because
  features are built test-first later.

Verify it yourself before stopping: run the install and build commands, start the server with
`PORT=3999`, `curl http://127.0.0.1:3999<health_path>`, then stop the server. Leave no
processes running.
$delta_rules
When the contract files are valid and the skeleton starts, stop.
