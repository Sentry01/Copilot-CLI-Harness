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

function gitShowJson(ref, rel) {
  try {
    return JSON.parse(execFileSync('git', ['-C', ROOT, 'show', `${ref}:${rel}`], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }));
  } catch {
    return null;
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
    const ran = list.filter((s) => s !== 'skipped');
    const passes = ran.filter((s) => s === 'passed').length;
    outcomes.set(id, !ran.length ? 'skipped' : passes === ran.length ? 'passed' : passes === 0 ? 'failed' : 'flaky');
  }
  return { outcomes, unidentified, errors: (report.errors ?? []).map((e) => (e.message ?? String(e)).slice(0, 500)) };
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
  const baseline = readJson('harness/baseline.json', { tests: {}, retired: {} });
  const planned = new Map(plan.tests.map((t) => [t.id, t]));
  const active = plan.tests.filter((t) => (t.status ?? 'active') === 'active').map((t) => t.id);
  const retired = new Set([...plan.tests.filter((t) => t.status === 'retired').map((t) => t.id), ...Object.keys(baseline.retired ?? {})]);
  const baselineIds = Object.keys(baseline.tests ?? {}).filter((id) => !retired.has(id));

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
    const basePlan = gitShowJson(ref, 'harness/test_plan.json');
    const baseReqs = gitShowJson(ref, 'harness/requirements.json');
    const baseBaseline = gitShowJson(ref, 'harness/baseline.json');
    const baseLock = gitShowJson(ref, 'harness/tests.lock.json');
    for (const id of Object.keys(baseBaseline?.tests ?? {})) {
      if (!(id in (baseline.tests ?? {})) && !(id in (baseline.retired ?? {}))) failures.push(`baseline test ${id} was removed (only retirement through a PRD change is allowed)`);
    }
    for (const t of basePlan?.tests ?? []) {
      const now = planned.get(t.id);
      if (!now) failures.push(`planned test ${t.id} was removed`);
      else if (stable(t, ['status']) !== stable(now, ['status'])) failures.push(`planned test ${t.id} was edited (the plan is append-only)`);
    }
    const reqsNow = new Map((readJson('harness/requirements.json', { requirements: [] }).requirements).map((r) => [r.id, r]));
    for (const r of baseReqs?.requirements ?? []) {
      const now = reqsNow.get(r.id);
      if (!now) failures.push(`requirement ${r.id} was removed`);
      else if (stable(r, ['status', 'superseded_by']) !== stable(now, ['status', 'superseded_by'])) failures.push(`requirement ${r.id} was edited`);
    }
    if (baseLock && lock) {
      const known = new Set((baseLock.amendments ?? []).map((a) => `${a.test_id}|${a.decided_at}`));
      const newAmendments = (lock.amendments ?? []).filter((a) => !known.has(`${a.test_id}|${a.decided_at}`));
      const amendedFiles = new Set(newAmendments.map((a) => {
        const t = planned.get(a.test_id);
        return t ? `acceptance/specs/${t.category}/${t.group}.spec.ts` : '';
      }));
      for (const [rel, digest] of Object.entries(baseLock.files ?? {})) {
        if (!rel.startsWith('acceptance/specs/')) continue;
        if (lock.files[rel] !== undefined && lock.files[rel] !== digest && !amendedFiles.has(rel)) {
          failures.push(`frozen spec ${rel} changed without a recorded amendment`);
        }
        if (lock.files[rel] === undefined) failures.push(`frozen spec ${rel} was removed`);
      }
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
