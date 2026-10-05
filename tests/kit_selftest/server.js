// Fixture server for the acceptance-kit self-test: good endpoints plus deliberately broken ones.
const http = require('node:http');
const port = Number(process.env.PORT || 3000);
const host = process.env.HOST || '127.0.0.1';
const secure = {
  'Content-Security-Policy': "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'",
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer',
};
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
let attempts = 0;
const page = (body) => `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Kit</title>
<style>button:focus-visible, input:focus-visible { outline: 3px solid #1a73e8; } button { min-width: 44px; min-height: 44px; }</style>
</head><body><main><h1>Kit fixture</h1>${body}</main></body></html>`;

http.createServer((req, res) => {
  const url = new URL(req.url, 'http://x');
  const send = (status, body, type = 'application/json', extra = {}) => {
    res.writeHead(status, { ...secure, 'Content-Type': type, ...extra });
    res.end(typeof body === 'string' ? body : JSON.stringify(body));
  };
  switch (`${req.method} ${url.pathname}`) {
    case 'GET /health': return send(200, { status: 'ok' });
    case 'POST /__test__/reset': attempts = 0; return send(200, { ok: true });
    case 'GET /': return send(200, page('<form><label for="q">Search</label> <input id="q" name="q"> <button type="submit">Go</button></form>'), 'text/html');
    case 'GET /search': return send(200, page(`<p>Results for ${esc(url.searchParams.get('q'))}</p>`), 'text/html');
    case 'GET /unsafe-search': return send(200, page(`<p>Results for ${url.searchParams.get('q')}</p>`), 'text/html; charset=utf-8', { 'Content-Security-Policy': '' });
    case 'GET /wide': return send(200, page('<div style="width:2000px">wide</div>'), 'text/html');
    case 'GET /api/items': return send(200, [{ id: 1 }]);
    case 'POST /api/items': return send(422, { error: 'name is required' });
    case 'GET /api/private': return send(401, { error: 'Login required' });
    case 'POST /login':
      attempts += 1;
      if (attempts > 5) return send(429, { error: 'Too many attempts' });
      return send(200, { ok: true }, 'application/json', { 'Set-Cookie': 'sid=abc; HttpOnly; SameSite=Lax; Path=/' });
    case 'POST /weak-login': return send(200, { ok: true }, 'application/json', { 'Set-Cookie': 'sid=abc; Path=/' });
    case 'GET /leaky': return send(500, 'TypeError: x is undefined\n    at handler (/srv/app/node_modules/x/index.js:10:5)', 'text/plain');
    case 'GET /frame-star': return send(200, 'ok', 'text/plain', { 'Content-Security-Policy': "default-src 'self'; frame-ancestors *" });
    case 'POST /bogus-samesite-login': return send(200, { ok: true }, 'application/json', { 'Set-Cookie': 'sid=abc; HttpOnly; SameSite=bogus; Path=/' });
    case 'GET /blank': return send(200, '<!doctype html><html lang="en"><head><title>blank</title></head><body></body></html>', 'text/html');
    case 'GET /naked':
      res.writeHead(200, { 'Content-Type': 'text/plain', 'X-Powered-By': 'Express 4.1' });
      return res.end('no headers');
    default: return send(404, { error: 'Not found' });
  }
}).listen(port, host);
