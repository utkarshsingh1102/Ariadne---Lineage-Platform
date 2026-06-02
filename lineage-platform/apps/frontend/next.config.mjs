/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Carbon's Sass is published unprocessed and needs transpilation when
  // imported from a Next.js App Router project.
  transpilePackages: ["@carbon/react", "@carbon/icons-react", "@carbon/styles"],
  sassOptions: {
    silenceDeprecations: ["legacy-js-api", "import"],
  },
  output: "standalone",
};

export default nextConfig;
