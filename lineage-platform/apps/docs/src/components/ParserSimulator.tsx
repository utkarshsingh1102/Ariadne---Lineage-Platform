import React, { useEffect, useState } from 'react';
import BrowserOnly from '@docusaurus/BrowserOnly';
import useBaseUrl from '@docusaurus/useBaseUrl';
import CodeBlock from '@theme/CodeBlock';
import CytoscapeMini from './CytoscapeMini';

/**
 * Six-tab stepper that walks the reader through a single fixture's
 * parsing pipeline. Reads pre-computed snapshots from
 *   /simulations/<parser>/<fixture>/{input,tokens,tree,ast,ir,cypher,graph,meta}.*
 * (Some stages are parser-specific — e.g. tokens for ANTLR parsers,
 * AST for Spark, DOM for Tableau.) Snapshots are checked into the
 * static directory by scripts/build-simulations.mjs.
 */

type Stage = {
  key: string;
  label: string;
  file: string;
  language?: string;
  kind: 'code' | 'json' | 'tokens' | 'graph';
};

type SnapshotMeta = {
  parser: string;
  fixture: string;
  stats?: Record<string, number>;
  warnings?: { type: string; detail: string }[];
  parse_errors?: { line: number; column: number; detail: string }[];
};

type Props = {
  parser: 'tableau' | 'tws' | 'qlikview' | 'spark';
  fixture: string;
};

const STAGE_TEMPLATES: Record<string, Stage[]> = {
  // Each entry lists the candidate stage files in display order. The
  // component skips any whose file 404s, so the same template works for
  // parsers with slightly different pipelines.
  tableau: [
    { key: 'input',  label: 'Input',           file: 'input.twb',         kind: 'code',   language: 'xml' },
    { key: 'dom',    label: 'lxml DOM',        file: 'dom.json',          kind: 'json' },
    { key: 'ir',     label: 'IR',              file: 'ir.json',           kind: 'json' },
    { key: 'cypher', label: 'Cypher (dry-run)', file: 'cypher.cypher',    kind: 'code',   language: 'cypher' },
    { key: 'graph',  label: 'Graph',           file: 'graph.json',        kind: 'graph' },
  ],
  tws: [
    { key: 'input',  label: 'Input',           file: 'input.txt',         kind: 'code',   language: 'text' },
    { key: 'tokens', label: 'Tokens',          file: 'tokens.json',       kind: 'tokens' },
    { key: 'tree',   label: 'Parse tree',      file: 'tree.json',         kind: 'json' },
    { key: 'ir',     label: 'IR',              file: 'ir.json',           kind: 'json' },
    { key: 'cypher', label: 'Cypher (dry-run)', file: 'cypher.cypher',    kind: 'code',   language: 'cypher' },
    { key: 'graph',  label: 'Graph',           file: 'graph.json',        kind: 'graph' },
  ],
  qlikview: [
    { key: 'input',  label: 'Input',           file: 'input.qvs',         kind: 'code',   language: 'text' },
    { key: 'tokens', label: 'Tokens',          file: 'tokens.json',       kind: 'tokens' },
    { key: 'ir',     label: 'IR',              file: 'ir.json',           kind: 'json' },
    { key: 'cypher', label: 'Cypher (dry-run)', file: 'cypher.cypher',    kind: 'code',   language: 'cypher' },
    { key: 'graph',  label: 'Graph',           file: 'graph.json',        kind: 'graph' },
  ],
  spark: [
    { key: 'input',  label: 'Input',           file: 'input.py',          kind: 'code',   language: 'python' },
    { key: 'ast',    label: 'AST',             file: 'ast.json',          kind: 'json' },
    { key: 'ir',     label: 'IR',              file: 'ir.json',           kind: 'json' },
    { key: 'cypher', label: 'Cypher (dry-run)', file: 'cypher.cypher',    kind: 'code',   language: 'cypher' },
    { key: 'graph',  label: 'Graph',           file: 'graph.json',        kind: 'graph' },
  ],
};

async function fetchTextOrNull(url: string): Promise<string | null> {
  try {
    const r = await fetch(url);
    if (!r.ok) return null;
    return await r.text();
  } catch {
    return null;
  }
}

export default function ParserSimulator({ parser, fixture }: Props): React.ReactElement {
  return (
    <BrowserOnly fallback={<div style={{ minHeight: 320 }}>Loading simulator…</div>}>
      {() => <SimulatorInner parser={parser} fixture={fixture} />}
    </BrowserOnly>
  );
}

function SimulatorInner({ parser, fixture }: Props): React.ReactElement {
  const baseUrl = useBaseUrl(`/simulations/${parser}/${fixture}/`);
  const candidates = STAGE_TEMPLATES[parser] ?? [];

  const [activeKey, setActiveKey] = useState<string>(candidates[0]?.key ?? '');
  const [meta, setMeta] = useState<SnapshotMeta | null>(null);
  const [stages, setStages] = useState<(Stage & { body: string })[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const metaText = await fetchTextOrNull(`${baseUrl}meta.json`);
        if (!metaText) {
          setError(`No snapshots found for ${parser}/${fixture}. ` +
            `Run \`npm run build:simulations\` from apps/docs/ with the parser containers up.`);
          return;
        }
        setMeta(JSON.parse(metaText));
        const present: (Stage & { body: string })[] = [];
        for (const s of candidates) {
          const body = await fetchTextOrNull(`${baseUrl}${s.file}`);
          if (body !== null) present.push({ ...s, body });
        }
        setStages(present);
        setActiveKey(present[0]?.key ?? '');
      } catch (e: any) {
        setError(String(e?.message ?? e));
      }
    })();
  }, [baseUrl, parser, fixture]);

  if (error) return <div className="simulator-error" role="alert">{error}</div>;
  if (!meta) return <div>Loading…</div>;

  const active = stages.find(s => s.key === activeKey);

  return (
    <div className="parser-simulator">
      <div className="simulator-header">
        <strong>{parser}</strong> · <code>{meta.fixture}</code>
        {meta.stats && (
          <span className="simulator-stats">
            {Object.entries(meta.stats).map(([k, v]) => (
              <span key={k} className="stat-chip">
                <em>{k}</em>: <b>{v}</b>
              </span>
            ))}
          </span>
        )}
      </div>

      {meta.warnings && meta.warnings.length > 0 && (
        <details className="simulator-warnings">
          <summary>{meta.warnings.length} warning(s)</summary>
          <ul>
            {meta.warnings.map((w, i) => (
              <li key={i}><strong>{w.type}</strong>: {w.detail}</li>
            ))}
          </ul>
        </details>
      )}

      <div className="simulator-tabs" role="tablist">
        {stages.map(s => (
          <button
            key={s.key}
            role="tab"
            aria-selected={s.key === activeKey}
            className={`simulator-tab${s.key === activeKey ? ' active' : ''}`}
            onClick={() => setActiveKey(s.key)}
          >{s.label}</button>
        ))}
      </div>

      <div className="simulator-body" role="tabpanel">
        {active && renderStage(active)}
      </div>
    </div>
  );
}

function renderStage(stage: Stage & { body: string }): React.ReactElement {
  if (stage.kind === 'graph') {
    try {
      const data = JSON.parse(stage.body);
      return <CytoscapeMini elements={data} height={420} />;
    } catch {
      return <pre>{stage.body}</pre>;
    }
  }
  if (stage.kind === 'tokens') {
    try {
      const rows = JSON.parse(stage.body) as { line: number; column: number; type: string; text: string }[];
      return (
        <table className="simulator-tokens">
          <thead><tr><th>Line</th><th>Col</th><th>Type</th><th>Text</th></tr></thead>
          <tbody>
            {rows.filter(r => r.type !== 'WS').slice(0, 200).map((r, i) => (
              <tr key={i}>
                <td>{r.line}</td>
                <td>{r.column}</td>
                <td><code>{r.type}</code></td>
                <td><code>{(r.text || '').replace(/\n/g, '⏎')}</code></td>
              </tr>
            ))}
          </tbody>
        </table>
      );
    } catch {
      return <pre>{stage.body}</pre>;
    }
  }
  if (stage.kind === 'json') {
    let pretty = stage.body;
    try { pretty = JSON.stringify(JSON.parse(stage.body), null, 2); } catch {}
    return <CodeBlock language="json">{pretty}</CodeBlock>;
  }
  return <CodeBlock language={stage.language || 'text'}>{stage.body}</CodeBlock>;
}
