import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The site is static files on a host that cannot run anything, so the only
// thing the build needs to know is where the data directory sits next to it.
// `base: './'` keeps every asset reference relative, which is what lets the
// same build serve from a domain root or from a project path on GitHub Pages
// without being rebuilt for each.
export default defineConfig({
  base: './',
  plugins: [react()],
  build: {
    outDir: '../dist',
    emptyOutDir: true,
    assetsDir: 'assets',
  },
});
