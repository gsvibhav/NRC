# FBX character preview (local dev tool only)

Standalone Three.js viewer for inspecting `standing-greeting.fbx` in isolation. Not part of the production build or the live site's navigation.

## Run locally

From the `NRC Website` project root (so Vite resolves `three` from the existing `node_modules`):

```bash
npx vite tools/fbx-preview --port 5210
```

Then open `http://localhost:5210/fbx-preview.html`.
