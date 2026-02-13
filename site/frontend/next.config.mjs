import path from 'path';
import { fileURLToPath } from 'url';
import { withSentryConfig } from '@sentry/nextjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const pathToTailwind = path.join(__dirname, 'node_modules', 'tailwindcss');

/** @type {import('next').NextConfig} */
const nextConfig = {
  turbopack: {
    root: __dirname,
    resolveAlias: {
      tailwindcss: pathToTailwind,
      'tailwindcss/plugin': path.join(__dirname, 'node_modules', 'tailwindcss', 'plugin.js'),
    },
  },
  webpack: (config, { isServer }) => {
    config.resolve.alias = config.resolve.alias || {};
    config.resolve.alias.tailwindcss = pathToTailwind;
    return config;
  },
  redirects: async () => [
    { source: '/catalog', destination: '/browse', permanent: false },
  ],
  images: {
    remotePatterns: [
      { protocol: 'https', hostname: 'images.unsplash.com' },
      { protocol: 'https', hostname: 'pub-c5fa586b79dc4640bcd67692b7422a37.r2.dev' },
    ],
  },
};

export default withSentryConfig(nextConfig, {
  org: process.env.SENTRY_ORG || '',
  project: process.env.SENTRY_PROJECT || '',
  silent: !process.env.CI,
});
