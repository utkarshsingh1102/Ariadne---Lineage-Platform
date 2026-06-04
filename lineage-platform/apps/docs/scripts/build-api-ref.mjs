#!/usr/bin/env node
/**
 * Auto-generate API reference pages from each service's live /openapi.json
 * and the gateway's Cypher preset files.
 *
 * Produces:
 *   docs/reference/openapi/<service>.mdx       (per-service endpoint detail)
 *   docs/reference/api-catalogue.md            (master cross-service table)
 *   docs/reference/presets/<name>.mdx          (one card per .cypher preset)
 *   scripts/snapshots/openapi-<service>.json   (refreshed for offline builds)
 *   scripts/snapshots/presets.json             (preset name list)
 *
 * Run by `npm run build:api-ref` (also called by `prebuild`). Network
 * unreachable -> falls back to the checked-in snapshot.
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const APP_ROOT = path.resolve(__dirname, '..');
const DOCS_DIR = path.join(APP_ROOT, 'docs', 'reference');
const SNAP_DIR = path.join(__dirname, 'snapshots');
const PRESETS_DIR = path.resolve(
  APP_ROOT, '..', 'gateway', 'src', 'lineage_gateway', 'presets',
);

const SERVICES = [
  { name: 'gateway',  url: process.env.GATEWAY_URL  ?? 'http://localhost:8000' },
  { name: 'tableau',  url: process.env.TABLEAU_URL  ?? 'http://localhost:8001' },
  { name: 'tws',      url: process.env.TWS_URL      ?? 'http://localhost:8002' },
  { name: 'qlikview', url: process.env.QLIKVIEW_URL ?? 'http://localhost:8003' },
  { name: 'spark',    url: process.env.SPARK_URL    ?? 'http://localhost:8004' },
];

const SERVICE_LABEL = {
  gateway:  'Gateway',
  tableau:  'Tableau parser',
  tws:      'TWS parser',
  qlikview: 'QlikView parser',
  spark:    'Spark parser',
};

const METHOD_BADGE = {
  get: 'GET', post: 'POST', put: 'PUT', delete: 'DELETE',
  patch: 'PATCH', options: 'OPTIONS', head: 'HEAD',
};

async function ensure(p) { await fs.mkdir(p, { recursive: true }); }

async function fetchOrFallback(name, url) {
  try {
    const r = await fetch(`${url}/openapi.json`, { signal: AbortSignal.timeout(4000) });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const spec = await r.json();
    // refresh snapshot
    await fs.writeFile(
      path.join(SNAP_DIR, `openapi-${name}.json`),
      JSON.stringify(spec, null, 2),
    );
    return { spec, live: true };
  } catch (e) {
    try {
      const snap = await fs.readFile(
        path.join(SNAP_DIR, `openapi-${name}.json`), 'utf8',
      );
      return { spec: JSON.parse(snap), live: false, err: String(e) };
    } catch (e2) {
      return { spec: { openapi: '3.0.0', info: { title: name, version: '0.0.0' }, paths: {} },
               live: false, err: `${e}; no snapshot (${e2})` };
    }
  }
}

function endpointsOf(spec) {
  const rows = [];
  for (const [pth, methods] of Object.entries(spec.paths ?? {})) {
    for (const [method, op] of Object.entries(methods)) {
      if (!METHOD_BADGE[method.toLowerCase()]) continue;
      rows.push({
        method: method.toUpperCase(),
        path: pth,
        summary: op.summary ?? op.description?.split('\n')[0]?.trim() ?? '',
        tags: op.tags ?? [],
        operationId: op.operationId ?? '',
      });
    }
  }
  // Stable order: by path then method.
  rows.sort((a, b) => (a.path === b.path
    ? a.method.localeCompare(b.method)
    : a.path.localeCompare(b.path)));
  return rows;
}

function renderPerServiceMdx(name, spec, live) {
  const rows = endpointsOf(spec);
  const status = live
    ? `_Generated live from \`${SERVICES.find(s => s.name === name).url}/openapi.json\`._`
    : `_Generated from checked-in snapshot (live service unreachable at build time)._`;
  const lines = [];
  lines.push(`---`);
  lines.push(`title: ${SERVICE_LABEL[name]} — OpenAPI`);
  lines.push(`sidebar_label: ${SERVICE_LABEL[name]}`);
  lines.push(`---`);
  lines.push('');
  lines.push(`# ${SERVICE_LABEL[name]} — OpenAPI reference`);
  lines.push('');
  lines.push(status);
  lines.push('');
  lines.push(`API title: **${spec.info?.title ?? name}**${spec.info?.version ? ` · v${spec.info.version}` : ''}`);
  lines.push('');
  lines.push(`**${rows.length}** endpoint${rows.length === 1 ? '' : 's'}.`);
  lines.push('');
  lines.push(`| Method | Path | Summary |`);
  lines.push(`|---|---|---|`);
  for (const r of rows) {
    const sum = (r.summary || '').replace(/\|/g, '\\|').replace(/\n/g, ' ');
    lines.push(`| \`${r.method}\` | \`${r.path}\` | ${sum} |`);
  }
  lines.push('');
  lines.push(`## Per-endpoint detail`);
  lines.push('');
  for (const r of rows) {
    lines.push(`### \`${r.method}\` \`${r.path}\``);
    if (r.summary) lines.push('');
    if (r.summary) lines.push(`> ${r.summary}`);
    if (r.tags?.length) lines.push(`Tags: ${r.tags.map(t => '`' + t + '`').join(', ')}`);
    if (r.operationId) lines.push(`Operation: \`${r.operationId}\``);
    lines.push('');
  }
  return lines.join('\n') + '\n';
}

function renderCatalogue(allRows) {
  const lines = [];
  lines.push(`---`);
  lines.push(`title: API catalogue`);
  lines.push(`sidebar_label: API catalogue`);
  lines.push(`---`);
  lines.push('');
  lines.push(`# API catalogue`);
  lines.push('');
  lines.push(`Every endpoint across every service in one searchable table. `
    + `**${allRows.length}** endpoints across **${new Set(allRows.map(r => r.service)).size}** services.`);
  lines.push('');
  lines.push(`Auto-generated from each service's \`/openapi.json\` by `
    + `[\`scripts/build-api-ref.mjs\`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/blob/main/lineage-platform/apps/docs/scripts/build-api-ref.mjs).`);
  lines.push('');
  lines.push(`| Service | Method | Path | Summary |`);
  lines.push(`|---|---|---|---|`);
  for (const r of allRows) {
    const sum = (r.summary || '').replace(/\|/g, '\\|').replace(/\n/g, ' ');
    const label = SERVICE_LABEL[r.service];
    const link = `[${label}](/reference/openapi/${r.service})`;
    lines.push(`| ${link} | \`${r.method}\` | \`${r.path}\` | ${sum} |`);
  }
  lines.push('');
  lines.push(`## See also`);
  lines.push('');
  lines.push(`- [Cypher presets](/reference/presets/lineage-upstream) — the read-only graph queries the gateway exposes.`);
  lines.push(`- [CLI reference](/reference/cli) — \`python -m <parser>_parser …\` invocations.`);
  lines.push('');
  return lines.join('\n') + '\n';
}

function renderPresetMdx(file, body) {
  const name = file.replace('.cypher', '');
  const lines = [];
  lines.push(`---`);
  lines.push(`title: ${name}`);
  lines.push(`sidebar_label: ${name}`);
  lines.push(`---`);
  lines.push('');
  lines.push(`# Cypher preset · \`${name}\``);
  lines.push('');
  lines.push(`Source: [\`apps/gateway/src/lineage_gateway/presets/${file}\`](https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/blob/main/lineage-platform/apps/gateway/src/lineage_gateway/presets/${file}).`);
  lines.push('');
  lines.push(`Invoke via the gateway:`);
  lines.push('');
  lines.push('```bash');
  lines.push(`curl -X POST "http://localhost:8000/graph/query/preset/${name}?node_id=<id>" \\`);
  lines.push(`  -H "content-type: application/json" -d '{}'`);
  lines.push('```');
  lines.push('');
  lines.push(`## Cypher`);
  lines.push('');
  lines.push('```cypher');
  lines.push(body.trimEnd());
  lines.push('```');
  lines.push('');
  return lines.join('\n') + '\n';
}

async function main() {
  await ensure(path.join(DOCS_DIR, 'openapi'));
  await ensure(path.join(DOCS_DIR, 'presets'));
  await ensure(SNAP_DIR);

  const allRows = [];
  let liveCount = 0;
  for (const s of SERVICES) {
    const { spec, live, err } = await fetchOrFallback(s.name, s.url);
    if (live) liveCount++;
    else console.warn(`  [warn] ${s.name}: using snapshot (${err ?? 'unreachable'})`);
    const rows = endpointsOf(spec);
    for (const r of rows) allRows.push({ ...r, service: s.name });
    await fs.writeFile(
      path.join(DOCS_DIR, 'openapi', `${s.name}.mdx`),
      renderPerServiceMdx(s.name, spec, live),
    );
    console.log(`  ${s.name}: ${rows.length} endpoints (${live ? 'live' : 'snapshot'})`);
  }

  await fs.writeFile(
    path.join(DOCS_DIR, 'api-catalogue.md'),
    renderCatalogue(allRows),
  );
  console.log(`  api-catalogue: ${allRows.length} rows`);

  // Cypher presets — file system is the source of truth, no live fetch needed.
  try {
    const presets = (await fs.readdir(PRESETS_DIR))
      .filter(f => f.endsWith('.cypher'));
    for (const f of presets) {
      const body = await fs.readFile(path.join(PRESETS_DIR, f), 'utf8');
      await fs.writeFile(
        path.join(DOCS_DIR, 'presets', f.replace('.cypher', '.mdx')),
        renderPresetMdx(f, body),
      );
    }
    await fs.writeFile(
      path.join(SNAP_DIR, 'presets.json'),
      JSON.stringify(presets, null, 2),
    );
    console.log(`  presets: ${presets.length} cypher files`);
  } catch (e) {
    console.warn(`  [warn] presets directory not reachable: ${e.message}`);
  }

  console.log(`build-api-ref done — ${liveCount}/${SERVICES.length} services live`);
}

main().catch(e => { console.error(e); process.exit(1); });
