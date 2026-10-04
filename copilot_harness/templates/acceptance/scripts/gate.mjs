#!/usr/bin/env node
/**
 * Acceptance regression gate (managed by copilot-harness). No dependencies.
 *
 *   node acceptance/scripts/gate.mjs --report acceptance/reports/report.json
 *        [--base-ref origin/main] [--strict] [--summary $GITHUB_STEP_SUMMARY]
 *
 * Fails when:
 *  1. the frozen suite drifted from harness/tests.lock.json (tests edited by hand),
 *  2. any baseline test is not passing (a regression) or did not run,
 *  3. the report contains tests that are not in the plan, or files failed to load,
 *  4. with --base-ref: the PR removed baseline/planned tests, edited existing plan or
 *     requirement entries, or changed a frozen spec file without a recorded amendment,
 *  5. with --strict: any active planned test is not passing (release gate).
 * Planned-but-unimplemented tests failing is expected (the TDD backlog) and does not fail
 * the gate.
 */
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { appendFileSync, existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const LOCKED_DIRS = ['acceptance'];
const LOCKED_FILES = ['harness/PRD.md', 'harness/requirements.json', 'harness/contract.json', 'harness/test_plan.json'];
const EXCLUDED = new Set(['node_modules', 'test-results', 'playwright-report', 'blob-report', 'reports', '.cache', '__pycache__']);
const ID_IN_TITLE = /^((?:FUNC|PERF|SEC|UX)-\d{3,4}):/;

function parseArgs(argv) {
  const args = { strict: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--strict') args.strict = true;
    else if (a.startsWith('--')) args[a.slice(2)] = argv[++i];
  }
  return args;
}

const readJson = (rel, fallback = null) => {
  const p = join(ROOT, rel);
  return existsSync(p) ? JSON.parse(readFileSync(p, 'utf8')) : fallback;
};
const sha256 = (p) => createHash('sha256').update(readFileSync(p)).digest('hex');
const posix = (p) => p.split(sep).join('/');

function walk(dir, out = []) {
  if (!existsSync(dir)) return out;
  for (const name of readdirSync(dir)) {
    if (EXCLUDED.has(name)) continue;
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else out.push(posix(relative(ROOT, p)));
  }
  return out;
}

function lockedFiles() {
  const files = new Set();
  for (const d of LOCKED_DIRS) walk(join(ROOT, d)).forEach((f) => files.add(f));
  for (const f of LOCKED_FILES) if (existsSync(join(ROOT, f))) files.add(f);
  const changes = join(ROOT, 'harness', 'changes');
  if (existsSync(changes)) for (const n of readdirSync(changes)) if (n.endsWith('.md')) files.add(`harness/changes/${n}`);
  const wf = join(ROOT, '.github', 'workflows');
  if (existsSync(wf)) for (const n of readdirSync(wf)) if (/^acceptance.*\.yml$/.test(n)) files.add(`.github/workflows/${n}`);
  return [...files].sort();
}

function gitShowText(ref, rel) {
  try {
    return execFileSync('git', ['-C', ROOT, 'show', `${ref}:${rel}`], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] });
  } catch {
    return null;
  }
}

function gitShowJson(ref, rel) {
  const text = gitShowText(ref, rel);
  return text === null ? null : JSON.parse(text);
}

function refResolves(ref) {
  try {
    execFileSync('git', ['-C', ROOT, 'rev-parse', '--verify', '--quiet', `${ref}^{commit}`], { stdio: 'ignore' });
    return true;
  } catch {
    return false;
  }
}

function aggregate(report) {
  const raw = new Map();
  const files = new Map();
  const unidentified = [];
  const visit = (suite) => {
    for (const spec of suite.specs ?? []) {
      const m = ID_IN_TITLE.exec(spec.title ?? '');
      if (!m) { unidentified.push(spec.title); continue; }
      const id = m[1];
      if (!files.has(id)) files.set(id, spec.file);
      const list = raw.get(id) ?? [];
      for (const t of spec.tests ?? []) {
        const results = t.results ?? [];
        if (!results.length) { list.push('skipped'); continue; }
        const last = results[results.length - 1].status;
        const verdict = last === 'passed' ? 'passed' : last === 'skipped' ? 'skipped' : 'failed';
        if (verdict === 'passed' && results.slice(0, -1).some((r) => r.status !== 'passed' && r.status !== 'skipped')) list.push('failed');
        list.push(verdict);
      }
      raw.set(id, list);
    }
    for (const child of suite.suites ?? []) visit(child);
  };
  for (const s of report.suites ?? []) visit(s);
  const outcomes = new Map();
  for (const [id, list] of raw) {
    const passes = list.filter((s) => s === 'passed').length;
    const failures = list.filter((s) => s === 'failed').length;
    // A skipped repetition is never a pass: all runs must execute and pass.
    outcomes.set(id, passes === list.length ? 'passed' : failures && passes ? 'flaky' : failures ? 'failed' : 'skipped');
  }
  return { outcomes, unidentified, errors: (report.errors ?? []).map((e) => (e.message ?? String(e)).slice(0, 500)) };
}

const HARNESS_RECORDS = new Set(['harness/requirements.json', 'harness/test_plan.json', 'harness/contract.json', 'harness/PRD.md']);

/** Compare the PR head against the base branch: nothing frozen may be weakened or removed. */
function ratchet(ref, { lock, baseline, retiredNow, plan, planned, failures }) {
  const basePlan = gitShowJson(ref, 'harness/test_plan.json');
  const baseReqs = gitShowJson(ref, 'harness/requirements.json');
  const baseBaseline = gitShowJson(ref, 'harness/baseline.json');
  const baseLock = gitShowJson(ref, 'harness/tests.lock.json');
  const reqsNow = new Map((readJson('harness/requirements.json', { requirements: [] }).requirements).map((r) => [r.id, r]));

  for (const id of Object.keys(baseBaseline?.tests ?? {})) {
    if (!(id in baseline) && !(id in retiredNow)) failures.push(`baseline test ${id} was removed (only retirement through a PRD change is allowed)`);
  }
  for (const t of basePlan?.tests ?? []) {
    const now = planned.get(t.id);
    if (!now) { failures.push(`planned test ${t.id} was removed`); continue; }
    if (stable(t, ['status']) !== stable(now, ['status'])) failures.push(`planned test ${t.id} was edited (the plan is append-only)`);
    if ((t.status ?? 'active') === 'active' && now.status === 'retired') {
      const live = (now.req_ids ?? []).filter((rid) => (reqsNow.get(rid)?.status ?? 'active') === 'active');
      if (live.length) failures.push(`planned test ${t.id} was retired but its requirements are still active: ${live.join(', ')}`);
    }
  }
  for (const r of baseReqs?.requirements ?? []) {
    const now = reqsNow.get(r.id);
    if (!now) failures.push(`requirement ${r.id} was removed`);
    else if (stable(r, ['status', 'superseded_by']) !== stable(now, ['status', 'superseded_by'])) failures.push(`requirement ${r.id} was edited`);
  }
  const baseContract = gitShowJson(ref, 'harness/contract.json');
  if (baseContract) {
    const now = readJson('harness/contract.json', {});
    const api = new Set((now.api ?? []).map((e) => `${e.method} ${e.path}`));
    for (const e of baseContract.api ?? []) if (!api.has(`${e.method} ${e.path}`)) failures.push(`contract endpoint ${e.method} ${e.path} was removed`);
    const routes = new Set((now.ui?.routes ?? []).map((r) => r.path));
    for (const r of baseContract.ui?.routes ?? []) if (!routes.has(r.path)) failures.push(`contract route ${r.path} was removed`);
    for (const id of Object.keys(baseContract.ui?.testids ?? {})) if (!(id in (now.ui?.testids ?? {}))) failures.push(`contract test id ${id} was removed`);
  }
  const basePrd = gitShowText(ref, 'harness/PRD.md');
  if (basePrd !== null) {
    const prd = existsSync(join(ROOT, 'harness/PRD.md')) ? readFileSync(join(ROOT, 'harness/PRD.md'), 'utf8') : '';
    if (!prd.startsWith(basePrd.trimEnd())) failures.push('harness/PRD.md was edited (changes are appended through `copilot-harness feature`)');
  }
  if (!baseLock || !lock) return;

  const seen = (list) => new Set((list ?? []).map((a) => `${a.test_id ?? a.path}|${a.decided_at}`));
  const baseAmend = seen(baseLock.amendments);
  const amendedFiles = new Set((lock.amendments ?? []).filter((a) => !baseAmend.has(`${a.test_id}|${a.decided_at}`)).map((a) => {
    const t = planned.get(a.test_id);
    return t ? `acceptance/specs/${t.category}/${t.group}.spec.ts` : '';
  }));
  const baseKit = seen(baseLock.kit_changes);
  const authorizedKit = new Set((lock.kit_changes ?? []).filter((k) => !baseKit.has(`${k.path}|${k.decided_at}`)).map((k) => k.path));

  const paths = new Set([...Object.keys(baseLock.files ?? {}), ...Object.keys(lock.files ?? {})]);
  for (const rel of paths) {
    const before = baseLock.files?.[rel];
    const after = lock.files?.[rel];
    if (before === after) continue;
    if (rel.startsWith('acceptance/specs/')) {
      if (before === undefined) continue; // new spec files: their tests must be planned (checked above)
      if (after === undefined) failures.push(`frozen spec ${rel} was removed`);
      else if (!amendedFiles.has(rel)) failures.push(`frozen spec ${rel} changed without a recorded amendment`);
    } else if (HARNESS_RECORDS.has(rel)) {
      continue; // append-only checks above
    } else if (rel.startsWith('harness/changes/')) {
      if (before !== undefined) failures.push(`change request ${rel} was ${after === undefined ? 'removed' : 'edited'}`);
    } else if (!authorizedKit.has(rel)) {
      const what = before === undefined ? 'added' : after === undefined ? 'removed' : 'changed';
      failures.push(`frozen kit file ${rel} was ${what} without authorization (run \`copilot-harness relock --reason ...\`)`);
    }
  }
}

const stable = (o, omit = []) => JSON.stringify(Object.fromEntries(Object.entries(o).filter(([k]) => !omit.includes(k)).sort()));

function main() {
  const args = parseArgs(process.argv.slice(2));
  const failures = [];
  const notes = [];

  // 1. Lock integrity
  const lock = readJson('harness/tests.lock.json');
  if (!lock) failures.push('harness/tests.lock.json is missing: the suite was never frozen by copilot-harness');
  else {
    const current = lockedFiles();
    for (const [rel, digest] of Object.entries(lock.files)) {
      if (!existsSync(join(ROOT, rel))) failures.push(`frozen file deleted: ${rel}`);
      else if (sha256(join(ROOT, rel)) !== digest) failures.push(`frozen file modified without re-freezing: ${rel}`);
    }
    for (const rel of current) if (!(rel in lock.files)) failures.push(`unfrozen file inside the acceptance suite: ${rel}`);
  }

  const plan = readJson('harness/test_plan.json', { tests: [] });
  const baseline = readJson('harness/baseline.json');
  if (lock && !baseline) failures.push('harness/baseline.json is missing: the regression baseline cannot be skipped');
  const baselineTests = baseline?.tests ?? {};
  const planned = new Map(plan.tests.map((t) => [t.id, t]));
  const active = plan.tests.filter((t) => (t.status ?? 'active') === 'active').map((t) => t.id);
  // Every baseline entry is enforced, whatever its plan status: authorized retirement moves it to baseline.retired.
  const baselineIds = Object.keys(baselineTests);

  // 2./3. Results
  let outcomes = new Map();
  if (!args.report || !existsSync(args.report)) failures.push(`test report not found: ${args.report ?? '(pass --report)'}`);
  else {
    const agg = aggregate(JSON.parse(readFileSync(args.report, 'utf8')));
    outcomes = agg.outcomes;
    for (const e of agg.errors) failures.push(`suite failed to load: ${e}`);
    for (const t of agg.unidentified) failures.push(`test without a planned ID: "${t}"`);
    for (const id of outcomes.keys()) if (!planned.has(id)) failures.push(`${id} is not in harness/test_plan.json`);
    for (const id of baselineIds) {
      const s = outcomes.get(id);
      if (s !== 'passed') failures.push(`REGRESSION ${id}: ${s ?? 'did not run'} — ${planned.get(id)?.title ?? ''}`);
    }
  }

  // 4. Ratchet against the base branch
  if (args['base-ref']) {
    const ref = args['base-ref'];
    if (!refResolves(ref)) {
      failures.push(`base ref ${ref} cannot be resolved; fetch it (actions/checkout fetch-depth: 0) so the ratchet can run`);
    } else {
      ratchet(ref, { lock, baseline: baselineTests, retiredNow: baseline?.retired ?? {}, plan, planned, failures });
    }
  }

  // 5. Backlog / strict
  const pending = active.filter((id) => !baselineIds.includes(id));
  const nowPassing = pending.filter((id) => outcomes.get(id) === 'passed');
  const stillFailing = pending.filter((id) => outcomes.get(id) !== 'passed');
  if (args.strict) for (const id of stillFailing) failures.push(`${id} is not passing (strict mode): ${outcomes.get(id) ?? 'did not run'}`);
  if (nowPassing.length) notes.push(`${nowPassing.length} backlog test(s) now pass; run \`copilot-harness verify --promote\` to add them to the baseline: ${nowPassing.slice(0, 20).join(', ')}`);

  const passed = [...outcomes.values()].filter((s) => s === 'passed').length;
  const lines = [
    `## Acceptance gate: ${failures.length ? 'FAILED' : 'PASSED'}`,
    '',
    `| baseline (must pass) | passing now | planned (active) | backlog |`,
    `|---|---|---|---|`,
    `| ${baselineIds.length} | ${passed} | ${active.length} | ${stillFailing.length} |`,
    '',
    ...(failures.length ? ['### Failures', ...failures.slice(0, 100).map((f) => `- ${f}`), ''] : []),
    ...(notes.length ? ['### Notes', ...notes.map((n) => `- ${n}`), ''] : []),
  ];
  const text = lines.join('\n');
  console.log(text);
  if (args.summary) appendFileSync(args.summary, text + '\n');
  process.exit(failures.length ? 1 : 0);
}

main();
