---
title: Embedding lineage in your app
sidebar_label: Embedding lineage in your app
---

# Embedding lineage in your app

The gateway's API is the same whether you're calling it from the
Ariadne frontend or your own dashboard. Here's a minimal embed.

## The endpoints you need

| Endpoint | Use |
|---|---|
| `GET /files` | Inventory by source system. |
| `POST /graph/query/preset/lineage-upstream?node_id=<id>` | Upstream trace. |
| `POST /graph/query/preset/lineage-downstream?node_id=<id>` | Downstream trace. |
| `GET /graph/node/<id>/neighbors?depth=1` | Quick neighbour expansion. |
| `GET /graph/schema` | All labels + relationship types Neo4j knows about. |

## Minimal React example

```tsx
import { useEffect, useState } from 'react';

const GATEWAY = 'http://localhost:8000';

export function Lineage({ nodeId }: { nodeId: string }) {
  const [data, setData] = useState<{ nodes: any[]; edges: any[] } | null>(null);

  useEffect(() => {
    fetch(`${GATEWAY}/graph/query/preset/lineage-upstream?node_id=${nodeId}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: '{}',
    })
      .then(r => r.json())
      .then(setData);
  }, [nodeId]);

  if (!data) return <div>Loading…</div>;
  return <pre>{JSON.stringify({ nodes: data.nodes.length, edges: data.edges.length }, null, 2)}</pre>;
}
```

Render with Cytoscape, vis.js, d3-graphology, or anything else that
accepts a `{nodes, edges}` shape.

## CORS

The gateway honours `CORS_ALLOWED_ORIGINS`. Add your host to that
comma-separated list in `docker-compose.yml`:

```yaml
gateway:
  environment:
    CORS_ALLOWED_ORIGINS: http://localhost:3000,http://my-dashboard.local
```

Restart the gateway: `docker compose up -d --no-deps gateway`.

## Read-only Cypher

For custom queries: `POST /graph/query/cypher` with body
`{ "cypher": "MATCH (s:Schedule) RETURN s.name LIMIT 10", "parameters": {} }`.
The gateway runs the query through `cypher_guard.assert_read_only`
before sending it to Neo4j — `MATCH` / `RETURN` are allowed,
`CREATE` / `MERGE` / `DELETE` / `SET` are not.

## See also

- [Master API catalogue](/reference/api-catalogue) — everything you can call.
- [Cypher presets](/gateway/presets).
