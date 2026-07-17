import { defineConfig } from 'vite';

export default defineConfig({
  build: {
    rollupOptions: {
      input: {
        main: 'index.html',
        fbxPreview: 'fbx-preview.html',
        privacy: 'privacy.html',
      },
    },
  },
});
