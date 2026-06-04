#!/usr/bin/env node
/**
 * Walk each parser's tests/ tree, count fixtures and tests, write the
 * result to docs/tests/_matrix.json. The Tests overview page imports it
 * and renders the matrix.
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, '..', '..', '..', '..');
const OUT = path.resolve(__dirname, '..', 'docs', 'tests', '_matrix.json');

const PARSERS = ['tableau', 'tws', 'qlikview', 'spark'];

async function exists(p) { try { await fs.access(p); return true; } catch { return false; } }

async function walkCount(dir, predicate) {
  if (!(await exists(dir))) return 0;
  let n = 0;
  for (const ent of await fs.readdir(dir, { withFileTypes: true })) {
    const p = path.join(dir, ent.name);
    if (ent.isDirectory()) n += await walkCount(p, predicate);
    else if (predicate(ent.name)) n++;
  }
  return n;
}

async function main() {
  const rows = [];
  for (const parser of PARSERS) {
    const root = path.join(REPO_ROOT, `${parser}-parser`);
    const tests = path.join(root, 'tests');
    const fixtures = path.join(tests, 'fixtures');
    const altFixtures = path.join(root, 'fixtures');  // spark uses this layout
    rows.push({
      parser,
      tests: await walkCount(tests, n => /^test_.*\.py$/.test(n)),
      fixtures:
        (await walkCount(fixtures, () => true)) ||
        (await walkCount(altFixtures, () => true)),
    });
  }
  // Gateway tests
  const gwTests = path.join(REPO_ROOT, 'lineage-platform', 'apps', 'gateway', 'tests');
  rows.push({
    parser: 'gateway',
    tests: await walkCount(gwTests, n => /^test_.*\.py$/.test(n)),
    fixtures: 0,
  });

  await fs.mkdir(path.dirname(OUT), { recursive: true });
  await fs.writeFile(OUT, JSON.stringify(rows, null, 2));
  console.log(`build-test-matrix: ${rows.length} rows`);
  for (const r of rows) console.log(`  ${r.parser.padEnd(10)} tests=${r.tests} fixtures=${r.fixtures}`);
}

main().catch(e => { console.error(e); process.exit(1); });
