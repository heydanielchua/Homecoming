import { createReadStream, existsSync, statSync } from 'node:fs';
import { join, normalize } from 'node:path';
import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';

/**
 * Serve the built data directory during `npm run dev`.
 *
 * The site is static files beside a `data/` directory, and in production that
 * directory is simply there. In development it is not: `nhf site` writes it
 * to `../dist/data`, which is 148 MB and must not be copied into `public/` —
 * Vite would copy it into every build.
 *
 * So dev reads it where it lies. This is what makes designing against real
 * blocks possible: edit a component, see it hot-reload against 9,222 real
 * reports, without rebuilding any data.
 */
function serveBuiltData(root = '../dist/data'): Plugin {
  return {
    name: 'listing-check-dev-data',
    apply: 'serve',
    configureServer(server) {
      const base = normalize(join(server.config.root, root));
      if (!existsSync(base)) {
        server.config.logger.warn(
          `\n  No data at ${base}. The page will load but every lookup will ` +
            '404.\n  Build it with:  nhf site --out dist/data\n',
        );
      }
      server.middlewares.use((req, res, next) => {
        const url = (req.url ?? '').split('?')[0] ?? '';
        if (!url.startsWith('/data/')) return next();
        // Resolve, then check the result is still inside the data directory:
        // a dev server should not serve `/data/../../etc/passwd`.
        const target = normalize(join(base, url.slice('/data/'.length)));
        if (!target.startsWith(base) || !existsSync(target) || !statSync(target).isFile()) {
          res.statusCode = 404;
          res.end('not found');
          return;
        }
        res.setHeader('Content-Type', 'application/json; charset=utf-8');
        createReadStream(target).pipe(res);
      });
    },
  };
}

export default defineConfig({
  // Relative asset paths, so one build serves from a domain root or from a
  // project path on GitHub Pages without being rebuilt for either.
  base: './',
  plugins: [react(), serveBuiltData()],
  build: {
    outDir: '../dist',
    // Never true. `nhf site` writes 123 MB of JSON into dist/data, and a page
    // rebuild that emptied the directory would throw it away — which is the
    // whole point of being able to republish a design change in seconds.
    // `scripts/publish.sh` cleans the page's own files instead.
    emptyOutDir: false,
    assetsDir: 'assets',
  },
});
