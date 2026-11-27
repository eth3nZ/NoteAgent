import { defineConfig } from 'vite';

export default defineConfig({
  base: './',
  build: { outDir: '../app/web', emptyOutDir: true, sourcemap: false, chunkSizeWarningLimit: 1200,
    license: { fileName: 'assets/dependencies.txt' } },
});
