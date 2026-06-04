import type { SidebarsConfig } from '@docusaurus/plugin-content-docs';

// Mirrors the Information Architecture in the Phase 6 plan. Order is
// intentional — overview first, deepening to reference/tests last.

const sidebars: SidebarsConfig = {
  main: [
    {
      type: 'category',
      label: 'Overview',
      collapsed: false,
      items: [
        'overview/index',
        'overview/quick-start',
        'overview/glossary',
      ],
    },
    {
      type: 'category',
      label: 'Architecture',
      items: [
        'architecture/system',
        'architecture/data-flow',
        'architecture/contracts',
        'architecture/storage',
        'architecture/determinism',
      ],
    },
    {
      type: 'category',
      label: 'Tech stack',
      items: [
        'tech-stack/index',
        'tech-stack/per-service',
        'tech-stack/versions',
      ],
    },
    {
      type: 'category',
      label: 'Parsers',
      items: [
        'parsers/index',
        'parsers/tableau',
        'parsers/tws',
        'parsers/qlikview',
        'parsers/spark',
        'parsers/convergence',
        'parsers/architecture',
      ],
    },
    {
      type: 'category',
      label: 'Gateway',
      items: [
        'gateway/index',
        'gateway/endpoints',
        'gateway/presets',
      ],
    },
    {
      type: 'category',
      label: 'Frontend',
      items: [
        'frontend/index',
        'frontend/pages',
        'frontend/cytoscape',
        'frontend/lineage-trace',
      ],
    },
    {
      type: 'category',
      label: 'Contracts',
      items: ['contracts/index', 'contracts/fixtures'],
    },
    {
      type: 'category',
      label: 'Deploy',
      items: [
        'deploy/local',
        'deploy/aws',
        'deploy/operations',
      ],
    },
    {
      type: 'category',
      label: 'Reference',
      items: [
        'reference/api-catalogue',
        {
          type: 'category',
          label: 'OpenAPI',
          items: [
            'reference/openapi/gateway',
            'reference/openapi/tableau',
            'reference/openapi/tws',
            'reference/openapi/qlikview',
            'reference/openapi/spark',
          ],
        },
        {
          type: 'category',
          label: 'Cypher presets',
          items: [
            'reference/presets/lineage-upstream',
            'reference/presets/lineage-downstream',
            'reference/presets/qlikview-chart-lineage',
            'reference/presets/spark-connections',
            'reference/presets/spark-write-targets',
            'reference/presets/tableau-physical-tables',
          ],
        },
        'reference/cli',
      ],
    },
    {
      type: 'category',
      label: 'Tutorials',
      items: [
        'tutorials/parse-your-first-workbook',
        'tutorials/trace-spark-lineage',
        'tutorials/cross-system-impact-analysis',
        'tutorials/embedding-lineage-in-your-app',
        'tutorials/see-the-parser-work',
      ],
    },
    {
      type: 'category',
      label: 'Tests',
      items: [
        'tests/index',
        'tests/tableau',
        'tests/tws',
        'tests/qlikview',
        'tests/spark',
      ],
    },
  ],
};

export default sidebars;
