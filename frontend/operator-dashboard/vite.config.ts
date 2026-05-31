import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { resolve } from 'node:path';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
  },
  // Skeleton package: build in library mode against the current entry
  // (src/index.ts exports VERSION). Switch to app mode (index.html +
  // React mount) once the real dashboard UI lands. See
  // planning/epics/12-observability.md.
  build: {
    lib: {
      entry: resolve(__dirname, 'src/index.ts'),
      formats: ['es'],
      fileName: 'kukiihome-operator-dashboard',
    },
    rollupOptions: {
      // Don't bundle React into the library output.
      external: ['react', 'react-dom'],
    },
  },
});
