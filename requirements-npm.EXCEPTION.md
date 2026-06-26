# npm lockfile — Coordinator-recordable Exception (W28A-861-R3)

**Decision: no npm lockfile is shipped for the publishable chat-client build.**

## Why

The chat-client publishable tree contains **no JavaScript/TypeScript build step**:

- The Web UI is shipped as a **pre-built static bundle** at `ui/dist/`
  (`index.html`, `assets/*.js`, `assets/*.css`, `runtime-config.js`).
- There is **no** `package.json`, `package-lock.json`, `vite.config.*`, `tsconfig`,
  or `*.ts`/`*.tsx`/`*.jsx` source tracked anywhere in the repository
  (`git ls-files | grep -iE 'package\.json|\.tsx?$|vite\.config' | grep -v node_modules` → 0 results, 2026-06-07).
- `Dockerfile.public` and `Dockerfile.chat-client` only `COPY ui/ ./ui/` — they do
  **not** run `npm install`, `npm ci`, or `vite build`, and pull from **no** npm
  registry (internal or public).

A lockfile locks a build's inputs. With no JS build in the publishable tree, an npm
lockfile would lock nothing and would be misleading. Python dependency
reproducibility is sealed by `requirements.lock`.

## If the UI source is ever published

If a future lane adds the UI **source** (the React/Vite project) to the publishable
tree, that lane MUST add `package-lock.json` (or equivalent) committed alongside the
source, and `Dockerfile.public` MUST run `npm ci` before `vite build` from the
checked-in lock — with no internal npm registry. Until then, the pre-built bundle is
the published artefact and this exception applies.
