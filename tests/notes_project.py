"""A tiny 'Notes' product used to drive the whole pipeline with scripted agents.

Each handler plays one agent role by writing the files that role is responsible for.
The coder deliberately ships an incomplete first attempt so the harness's stop hook has
to send it back to work.
"""

from __future__ import annotations

import json

from copilot_harness.backends.fake import FakeBackend, FakeTurn

PRD = """# Notes

A minimal notes service. People can add short text notes from a web page or via a JSON API
and see all notes listed on the home page. Empty notes are rejected. The page must be
accessible and fast, and the service must send standard security headers.
"""

DELTA = """Notes can be deleted via `DELETE /api/notes/:id`, which returns 204; deleting an
unknown id returns 404."""

REQUIREMENTS = {
    "version": 1,
    "product": "Notes",
    "summary": "Add and list short text notes via a web page and a JSON API.",
    "requirements": [
        {"id": "REQ-001", "title": "Create and list notes", "type": "functional", "priority": "P0",
         "description": "Users can create notes and see them listed.",
         "acceptance_criteria": ["POST /api/notes with text returns 201 and the note",
                                 "Empty text returns 422", "Notes added on / are listed"]},
        {"id": "REQ-002", "title": "Security headers", "type": "security", "priority": "P1",
         "description": "Every response carries standard security headers.",
         "acceptance_criteria": ["CSP, nosniff, frame-ancestors and Referrer-Policy are present"]},
        {"id": "REQ-003", "title": "Listing is fast", "type": "performance", "priority": "P1",
         "description": "GET /api/notes is fast.", "acceptance_criteria": ["p95 <= 200 ms"],
         "thresholds": [{"metric": "api_p95_ms", "operator": "<=", "value": 200, "unit": "ms"}]},
        {"id": "REQ-004", "title": "Accessible home page", "type": "usability", "priority": "P1",
         "description": "The home page meets WCAG 2.1 AA.", "acceptance_criteria": ["No serious axe violations"]},
    ],
}

CONTRACT = {
    "version": 1,
    "api": [
        {"method": "GET", "path": "/api/notes", "summary": "List notes", "responses": {"200": "[{id,text}]"}},
        {"method": "POST", "path": "/api/notes", "summary": "Create a note", "request": {"text": "string"},
         "responses": {"201": "{id,text}", "422": "{error}"}},
    ],
    "ui": {"routes": [{"path": "/", "name": "Home", "purpose": "List and add notes"}],
           "accessible_names": {"/": ["heading 'Notes'", "textbox 'Note'", "button 'Add note'"]}},
    "test_hooks": {"reset": "POST /__test__/reset"},
}

APP_CONTRACT = {
    "stack": "Node 22 http module, in-memory store",
    "install": [],
    "build": [],
    "start": "node app/server.js",
    "base_url": "http://127.0.0.1:3000",
    "health_path": "/health",
    "startup_timeout_seconds": 30,
}

SERVER_HEAD = r"""
const http = require('node:http');
const port = Number(process.env.PORT || 3000);
const host = process.env.HOST || '127.0.0.1';
const testMode = process.env.NODE_ENV === 'test' || process.env.APP_ENV === 'test';
let notes = [];
let nextId = 1;
const securityHeaders = {
  'Content-Security-Policy': "default-src 'self'; frame-ancestors 'none'",
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer',
};
const escape = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
function send(res, status, body, type = 'application/json', extra = {}) {
  res.writeHead(status, { ...securityHeaders, 'Content-Type': type, ...extra });
  res.end(type === 'application/json' ? JSON.stringify(body) : body);
}
function readBody(req) {
  return new Promise((resolve) => { let b = ''; req.on('data', (c) => (b += c)); req.on('end', () => resolve(b)); });
}
"""

SKELETON = SERVER_HEAD + r"""
http.createServer(async (req, res) => {
  if (req.method === 'GET' && req.url === '/health') return send(res, 200, { status: 'ok' });
  if (req.method === 'POST' && req.url === '/__test__/reset' && testMode) { notes = []; nextId = 1; return send(res, 200, { ok: true }); }
  send(res, 404, { error: 'Not found' });
}).listen(port, host);
"""


def implementation(validate: bool, delete: bool = False) -> str:
    validation = "if (!text.trim()) return send(res, 422, { error: 'Text is required' });" if validate else ""
    delete_route = r"""
  const m = req.url.match(/^\/api\/notes\/(\d+)$/);
  if (req.method === 'DELETE' && m) {
    const before = notes.length;
    notes = notes.filter((n) => n.id !== Number(m[1]));
    return notes.length < before ? send(res, 204, '', 'text/plain') : send(res, 404, { error: 'Not found' });
  }""" if delete else ""
    return SERVER_HEAD + r"""
function page() {
  const items = notes.map((n) => `<li>${escape(n.text)}</li>`).join('');
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Notes</title></head>
<body><main><h1>Notes</h1>
<form method="post" action="/notes"><label for="text">Note</label> <input id="text" name="text">
<button type="submit">Add note</button></form><ul>${items}</ul></main></body></html>`;
}
http.createServer(async (req, res) => {
  if (req.method === 'GET' && req.url === '/health') return send(res, 200, { status: 'ok' });
  if (req.method === 'POST' && req.url === '/__test__/reset' && testMode) { notes = []; nextId = 1; return send(res, 200, { ok: true }); }
  if (req.method === 'GET' && req.url === '/') return send(res, 200, page(), 'text/html; charset=utf-8');
  if (req.method === 'POST' && req.url === '/notes') {
    const text = new URLSearchParams(await readBody(req)).get('text') || '';
    if (text.trim()) notes.push({ id: nextId++, text });
    return send(res, 303, '', 'text/plain', { Location: '/' });
  }
  if (req.method === 'GET' && req.url === '/api/notes') return send(res, 200, notes);
  if (req.method === 'POST' && req.url === '/api/notes') {
    let text = '';
    try { text = String(JSON.parse(await readBody(req) || '{}').text ?? ''); } catch { return send(res, 400, { error: 'Invalid JSON' }); }
    """ + validation + r"""
    const note = { id: nextId++, text };
    notes.push(note);
    return send(res, 201, note);
  }""" + delete_route + r"""
  send(res, 404, { error: 'Not found' });
}).listen(port, host);
"""


def _case(id_, title, category, group, priority, kind, layer, reqs, expected, threshold=None, origin="PRD"):
    case = {"id": id_, "title": title, "category": category, "group": group, "priority": priority, "kind": kind,
            "layer": layer, "req_ids": reqs, "steps": ["see spec"], "expected": expected, "origin": origin}
    if threshold:
        case["threshold"] = threshold
    return case


PLAN = {
    "version": 1,
    "tests": [
        _case("FUNC-001", "creating a note via the API returns it", "functional", "notes", "P0", "positive", "api",
              ["REQ-001"], ["201 with the same text"]),
        _case("FUNC-002", "an empty note is rejected", "functional", "notes", "P0", "negative", "api",
              ["REQ-001"], ["422"]),
        _case("FUNC-003", "a note added in the page is listed", "functional", "notes", "P0", "positive", "e2e",
              ["REQ-001"], ["the note text is a list item"]),
        _case("SEC-001", "the home page sends security headers", "security", "headers", "P1", "positive", "api",
              ["REQ-002"], ["CSP, nosniff, frame-ancestors, Referrer-Policy"]),
        _case("PERF-001", "listing notes is fast", "performance", "notes-latency", "P1", "positive", "api",
              ["REQ-003"], ["p95 <= 200 ms"], {"metric": "api_p95_ms", "operator": "<=", "value": 200, "unit": "ms"}),
        _case("UX-001", "the home page has no serious accessibility violations", "usability", "home-a11y", "P1",
              "positive", "e2e", ["REQ-004"], ["no serious/critical axe violations"]),
    ],
}

SPECS = {
    "acceptance/specs/functional/notes.spec.ts": """import { test, expect } from '../../support';

test('FUNC-001: creating a note via the API returns it', async ({ request, data }) => {
  const text = data.id('note');
  const res = await request.post('/api/notes', { data: { text } });
  expect(res.status()).toBe(201);
  expect((await res.json()).text).toBe(text);
});

test('FUNC-002: an empty note is rejected', async ({ request }) => {
  const res = await request.post('/api/notes', { data: { text: '' } });
  expect(res.status()).toBe(422);
});

test('FUNC-003: a note added in the page is listed', async ({ page, data }) => {
  const text = data.id('note');
  await page.goto('/');
  await page.getByLabel('Note').fill(text);
  await page.getByRole('button', { name: 'Add note' }).click();
  await expect(page.getByRole('listitem').filter({ hasText: text })).toBeVisible();
});
""",
    "acceptance/specs/security/headers.spec.ts": """import { test, expectSecurityHeaders } from '../../support';

test('SEC-001: the home page sends security headers', async ({ request }) => {
  const res = await request.get('/');
  expectSecurityHeaders(res);
});
""",
    "acceptance/specs/performance/notes-latency.spec.ts": """import { test, measureLatency, expectLatency } from '../../support';

test('PERF-001: listing notes is fast', async ({ request }) => {
  const stats = await measureLatency(request, { path: '/api/notes' }, { samples: 10, warmup: 2 });
  expectLatency(stats, { p95: 200 });
});
""",
    "acceptance/specs/usability/home-a11y.spec.ts": """import { test, expect, expectNoA11yViolations } from '../../support';

test('UX-001: the home page has no serious accessibility violations', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Notes' })).toBeVisible();
  await expectNoA11yViolations(page);
});
""",
}

DELTA_SPEC = {
    "acceptance/specs/functional/notes-delete.spec.ts": """import { test, expect } from '../../support';

test('FUNC-004: deleting a note removes it', async ({ request, data }) => {
  const created = await (await request.post('/api/notes', { data: { text: data.id('n') } })).json();
  const res = await request.delete(`/api/notes/${created.id}`);
  expect(res.status()).toBe(204);
  const list = await (await request.get('/api/notes')).json();
  expect(list.map((n: { id: number }) => n.id)).not.toContain(created.id);
});

test('FUNC-005: deleting an unknown note returns 404', async ({ request }) => {
  const res = await request.delete('/api/notes/999999');
  expect(res.status()).toBe(404);
});
""",
}


def make_backend() -> FakeBackend:
    def analyst(t: FakeTurn) -> None:
        change = t.spec.prompt.split("This is change ")[1][:7] if "This is change " in t.spec.prompt else ""
        doc = json.loads(json.dumps(REQUIREMENTS))
        if change:
            doc["requirements"].append({
                "id": "REQ-005", "title": "Delete notes", "type": "functional", "priority": "P0",
                "description": "Notes can be deleted.", "origin": change,
                "acceptance_criteria": ["DELETE /api/notes/:id returns 204", "Unknown id returns 404"]})
        t.write("harness/requirements.json", json.dumps(doc, indent=2))

    def architect(t: FakeTurn) -> None:
        contract = json.loads(json.dumps(CONTRACT))
        if "This is change" in t.spec.prompt:
            contract["api"].append({"method": "DELETE", "path": "/api/notes/{id}", "summary": "Delete a note",
                                    "responses": {"204": "", "404": "{error}"}})
        else:
            t.write("app/server.js", SKELETON)
        t.write("harness/contract.json", json.dumps(contract, indent=2))
        t.write("harness/app-contract.json", json.dumps(APP_CONTRACT, indent=2))

    def test_architect(t: FakeTurn) -> None:
        plan = json.loads(json.dumps(PLAN))
        if "This is change" in t.spec.prompt:
            plan["tests"] += [
                _case("FUNC-004", "deleting a note removes it", "functional", "notes-delete", "P0", "positive", "api",
                      ["REQ-005"], ["204 then absent from the list"], origin="CHG-001"),
                _case("FUNC-005", "deleting an unknown note returns 404", "functional", "notes-delete", "P0",
                      "negative", "api", ["REQ-005"], ["404"], origin="CHG-001"),
            ]
        t.write("harness/test_plan.json", json.dumps(plan, indent=2))

    def plan_reviewer(t: FakeTurn) -> None:
        t.write(".harness/reviews/plan-review.json", json.dumps({"verdict": "approve", "issues": []}))

    def test_author(t: FakeTurn) -> None:
        if t.spec.prompt.count("Vacuity review"):
            return  # SEC-001 genuinely holds for the skeleton; leave it unchanged
        wanted = DELTA_SPEC if "FUNC-004" in t.spec.prompt else SPECS
        for rel, src in wanted.items():
            if any(f'"spec_file": "{rel}"' in t.spec.prompt for _ in [0]):
                t.write(rel, src)

    def coder(t: FakeTurn) -> None:
        delete = "FUNC-004" in t.spec.prompt or "FUNC-005" in t.spec.prompt
        # First attempt forgets validation; the stop hook sends the agent back to work.
        t.write("app/server.js", implementation(validate=t.turn > 0 or delete, delete=delete))

    return FakeBackend(handlers={
        "analyst": analyst, "architect": architect, "test_architect": test_architect,
        "plan_reviewer": plan_reviewer, "test_author": test_author, "coder": coder,
    })
