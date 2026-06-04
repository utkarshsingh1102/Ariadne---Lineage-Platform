import React, { useEffect, useRef } from 'react';
import BrowserOnly from '@docusaurus/BrowserOnly';

/**
 * Tiny Cytoscape renderer for the docs site. Visual style mirrors the
 * platform frontend's _lib/cytoscape-config.ts so screenshots and embedded
 * widgets look consistent.
 *
 * Reusable. Pass {nodes,edges} in the same shape the gateway returns
 * (data wrapper around each node/edge), and an optional height.
 */
type Elements = {
  nodes: Array<{ data: { id: string; label?: string; labels?: string[]; source_system?: string; properties?: Record<string, unknown> } }>;
  edges: Array<{ data: { id: string; source: string; target: string; label?: string; properties?: Record<string, unknown> } }>;
};

const SOURCE_COLOR: Record<string, string> = {
  tableau: '#1f6fbf',
  qlikview: '#009844',
  tws: '#d12771',
  spark: '#e67e22',
  shared: '#8a3ffc',
};

export default function CytoscapeMini({ elements, height = 360 }: { elements: Elements; height?: number }) {
  return (
    <BrowserOnly fallback={<div style={{ minHeight: height }}>Loading graph…</div>}>
      {() => <Inner elements={elements} height={height} />}
    </BrowserOnly>
  );
}

function Inner({ elements, height }: { elements: Elements; height: number }) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cy: any;
    (async () => {
      const cytoscape = (await import('cytoscape')).default;
      try {
        const elk = (await import('cytoscape-elk')).default;
        cytoscape.use(elk);
      } catch {/* fall back to cose layout */}

      if (!containerRef.current) return;
      cy = cytoscape({
        container: containerRef.current,
        elements: {
          nodes: elements.nodes.map(n => ({
            data: { ...n.data, _color: SOURCE_COLOR[n.data.source_system ?? 'shared'] ?? '#525252' },
          })),
          edges: elements.edges.map(e => ({ data: e.data })),
        },
        style: [
          {
            selector: 'node',
            style: {
              'background-color': 'data(_color)',
              'label': 'data(label)',
              'color': '#161616',
              'font-size': 10,
              'text-margin-y': -6,
              'text-valign': 'top',
              'text-halign': 'center',
              'width': 28, 'height': 28,
              'border-width': 1,
              'border-color': '#393939',
            },
          },
          {
            selector: 'edge',
            style: {
              'curve-style': 'bezier',
              'target-arrow-shape': 'triangle',
              'width': 1.5,
              'line-color': '#a8a8a8',
              'target-arrow-color': '#a8a8a8',
              'label': 'data(label)',
              'font-size': 8,
              'color': '#525252',
              'text-rotation': 'autorotate',
            },
          },
        ],
        layout: { name: 'elk', elk: { algorithm: 'layered' } } as any,
      });
      // If elk isn't available the layout silently fails; reapply cose as a fallback.
      try { cy.fit(undefined, 24); } catch {}
    })();
    return () => { try { cy?.destroy(); } catch {} };
  }, [elements]);

  return <div ref={containerRef} style={{
    width: '100%',
    height,
    border: '1px solid var(--ifm-color-emphasis-300)',
    borderRadius: 4,
    background: 'var(--ifm-background-surface-color)',
  }} />;
}
