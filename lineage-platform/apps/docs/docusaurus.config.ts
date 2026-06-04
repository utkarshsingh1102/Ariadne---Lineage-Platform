import type { Config } from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';
import { themes as prismThemes } from 'prism-react-renderer';

// Phase 6 site config. Kept config-as-code so every choice is reviewable.
// All public-facing copy lives here; navigation lives in sidebars.ts.

const config: Config = {
  title: 'Ariadne Lineage',
  tagline:
    'Unified knowledge graph for Tableau, TWS, QlikView and Spark — one lineage, four parsers.',
  favicon: 'img/favicon.svg',

  // Production URL is provided via env so the same image runs locally and on EC2.
  url: process.env.DOCS_PUBLIC_URL ?? 'http://localhost:3002',
  baseUrl: '/',

  organizationName: 'utkarshsingh1102',
  projectName: 'Ariadne---Lineage-Platform',

  onBrokenLinks: 'warn',

  i18n: { defaultLocale: 'en', locales: ['en'] },

  markdown: {
    mermaid: true,
    hooks: {
      onBrokenMarkdownLinks: 'warn',
      onBrokenMarkdownImages: 'warn',
    },
  },
  themes: ['@docusaurus/theme-mermaid'],

  presets: [
    [
      'classic',
      {
        docs: {
          path: 'docs',
          routeBasePath: '/',
          sidebarPath: './sidebars.ts',
          editUrl:
            'https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform/edit/main/lineage-platform/apps/docs/',
        },
        blog: false,
        theme: { customCss: './src/css/custom.css' },
      } satisfies Preset.Options,
    ],
  ],

  plugins: [
    [
      // Offline-first search until Algolia DocSearch is approved (sub-phase 6.6).
      '@easyops-cn/docusaurus-search-local',
      {
        hashed: true,
        indexBlog: false,
        docsRouteBasePath: '/',
        highlightSearchTermsOnTargetPage: true,
      },
    ],
  ],

  themeConfig: {
    image: 'img/social-card.png',
    colorMode: { defaultMode: 'light', respectPrefersColorScheme: true },
    navbar: {
      title: 'Ariadne Lineage',
      logo: { alt: 'Ariadne', src: 'img/logo.svg' },
      items: [
        { to: '/', label: 'Overview', position: 'left' },
        { to: '/architecture/system', label: 'Architecture', position: 'left' },
        { to: '/parsers/', label: 'Parsers', position: 'left' },
        { to: '/reference/api-catalogue', label: 'API', position: 'left' },
        { to: '/tutorials/parse-your-first-workbook', label: 'Tutorials', position: 'left' },
        {
          href: 'https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform',
          label: 'GitHub',
          position: 'right',
        },
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: 'Docs',
          items: [
            { label: 'Overview', to: '/' },
            { label: 'Architecture', to: '/architecture/system' },
            { label: 'Parsers', to: '/parsers/' },
            { label: 'Tech stack', to: '/tech-stack/' },
          ],
        },
        {
          title: 'Reference',
          items: [
            { label: 'API catalogue', to: '/reference/api-catalogue' },
            { label: 'Cypher presets', to: '/reference/presets/lineage-upstream' },
            { label: 'CLI', to: '/reference/cli' },
          ],
        },
        {
          title: 'More',
          items: [
            {
              label: 'GitHub',
              href: 'https://github.com/utkarshsingh1102/Ariadne---Lineage-Platform',
            },
            { label: 'Deploy', to: '/deploy/aws' },
          ],
        },
      ],
      copyright: `Built with Docusaurus 3. © ${new Date().getFullYear()} Ariadne Lineage Platform.`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
      additionalLanguages: ['python', 'bash', 'cypher', 'sql', 'json', 'yaml', 'xml-doc'],
    },
    mermaid: {
      theme: { light: 'neutral', dark: 'dark' },
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
