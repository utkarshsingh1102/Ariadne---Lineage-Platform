#!/usr/bin/env node
/**
 * Produce the pre-computed snapshots that feed the <ParserSimulator/>
 * widget. For each canonical fixture across the four parsers, runs the
 * `cli.dump_stages` module via `docker exec` against the running parser
 * container and copies the resulting JSON files back to
 *   apps/docs/static/simulations/<parser>/<fixture-slug>/
 *
 * Snapshots are CHECKED IN to git. The docs Docker build does not need
 * the parser containers to be running — it just reads from the static
 * directory. This script is run by a developer when fixtures change.
 *
 * Usage:
 *   docker compose up -d tableau-parser tws-parser qlikview-parser spark-parser
 *   npm run build:simulations
 */
import { execSync } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const STATIC_DIR = path.resolve(__dirname, '..', 'static', 'simulations');

// Per-parser config: container name, the module path, the fixture
// path INSIDE the container's /data/inputs mount, and the on-host
// slug for the output directory.
const JOBS = [
  {
    parser: 'tws',
    container: 'lineage-tws-parser',
    module: 'tws_parser.cli.dump_stages',
    fixtures: [
      { slug: '01_single_schedule_single_job',
        inside: '/data/inputs/01_single_schedule_single_job.txt' },
      { slug: '06_realistic_dump_many_schedules',
        inside: '/data/inputs/06_realistic_dump_many_schedules.txt' },
    ],
  },
  {
    parser: 'tableau',
    container: 'lineage-tableau-parser',
    module: 'tableau_parser.cli.dump_stages',
    fixtures: [
      { slug: '01_simple_single_datasource',
        inside: '/data/inputs/01_simple_single_datasource.twb' },
      { slug: '02_calculated_fields',
        inside: '/data/inputs/02_calculated_fields.twb' },
    ],
  },
  {
    parser: 'qlikview',
    container: 'lineage-qlikview-parser',
    module: 'qlikview_parser.cli.dump_stages',
    fixtures: [
      { slug: '01_simple_sql_load',
        inside: '/data/inputs/01_simple_sql_load.qvs' },
      { slug: '03_left_join',
        inside: '/data/inputs/03_left_join.qvs' },
    ],
  },
  {
    parser: 'spark',
    container: 'lineage-spark-parser',
    module: 'spark_parser.cli.dump_stages',
    fixtures: [
      { slug: '01_simple_read_write',
        inside: '/data/inputs/pyspark/01_simple_read_write.py' },
      { slug: '02_join_and_select',
        inside: '/data/inputs/pyspark/02_join_and_select.py' },
    ],
  },
];

async function ensure(p) { await fs.mkdir(p, { recursive: true }); }

function exec(cmd) {
  try {
    return execSync(cmd, { stdio: ['ignore', 'pipe', 'pipe'] }).toString();
  } catch (e) {
    return { error: e.stderr?.toString() || e.message };
  }
}

async function dumpOne(job, fix) {
  const outHost = path.join(STATIC_DIR, job.parser, fix.slug);
  await ensure(outHost);

  const insideOut = `/tmp/sim-out-${job.parser}-${fix.slug}`;
  // Run dump_stages inside the container.
  const r = exec(
    `docker exec ${job.container} python -m ${job.module} ${fix.inside} --out ${insideOut}`
  );
  if (typeof r === 'object' && r.error) {
    console.warn(`  [skip] ${job.parser}/${fix.slug}: ${r.error.split('\n').slice(-3).join(' | ').slice(0, 200)}`);
    return false;
  }
  // Copy results back to the host static dir.
  exec(`docker cp ${job.container}:${insideOut}/. ${outHost}`);
  exec(`docker exec ${job.container} rm -rf ${insideOut}`);

  // Sanity: at least meta.json should be present.
  try {
    await fs.access(path.join(outHost, 'meta.json'));
    return true;
  } catch {
    console.warn(`  [skip] ${job.parser}/${fix.slug}: no meta.json after copy`);
    return false;
  }
}

async function main() {
  await ensure(STATIC_DIR);
  let ok = 0, total = 0;
  for (const job of JOBS) {
    for (const fix of job.fixtures) {
      total++;
      const did = await dumpOne(job, fix);
      if (did) {
        ok++;
        console.log(`  ${job.parser}/${fix.slug} ✓`);
      }
    }
  }
  // Write an index for the React component to enumerate.
  const index = JOBS.map(j => ({
    parser: j.parser,
    fixtures: j.fixtures.map(f => f.slug),
  }));
  await fs.writeFile(
    path.join(STATIC_DIR, 'index.json'), JSON.stringify(index, null, 2),
  );
  console.log(`build-simulations done — ${ok}/${total} fixtures produced`);
}

main().catch(e => { console.error(e); process.exit(1); });
