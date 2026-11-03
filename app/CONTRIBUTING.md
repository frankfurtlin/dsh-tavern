# Contributing

Please open an issue describing the user-visible problem before a large change. Keep PRs focused and include relevant validation.

- Node.js 22, Python 3.12 or 3.13, PostgreSQL 16.
- Frontend: `cd client && npm ci && npm run typecheck && npm run test:unit && npm run build`.
- Backend: use a **new dedicated database whose name ends in `_test`**, `AI_MODE=mock`, and private temporary storage. Tests deliberately truncate their test database. The CI workflow gives a complete example.
- Do not submit real model keys, real user conversations, production URLs or private save files.
- Changes to world actions, memory, routes or portraits must preserve canonical checkpoints and keep world-external chat isolated from the main story.

This release is a web beta. Native projects are included as development source; a successful web build does not certify Android or iOS builds.
