# First open-source snapshot · 2026-10-26

Tag: `v0.1.0-beta.1`.

This is an independently prepared snapshot of the current web beta, including unlimited product quotas, product telemetry, and the standalone administrator analytics site.

## Verified on the exported source

| Check | Result |
| --- | --- |
| Fresh `npm ci` | Passed |
| Frontend type check | Passed |
| Frontend unit tests | 167 files / 1,461 tests passed |
| Frontend production build, including insights entry | Passed |
| Backend regression in a newly created isolated PostgreSQL database | 349 tests passed |
| Fresh database migrations and world schema | Passed |
| Registration, explicit CLI administrator grant, sample seeding | Passed; 4 sample works available |
| Default mock model, unlimited quota, admin analytics access | Passed |
| Generated `.env`, refusal to overwrite, Compose configuration validation | Passed |
| Gitleaks scan of the publication source | No findings; only seven explicitly annotated synthetic fixture constants are exempted |
| Showcase images | Five images rendered from original covers and anonymized real UI screenshots |

The local Docker daemon was unavailable, so this record does **not** certify an end-to-end Docker build/start. The equivalent backend initialization and application paths were exercised natively. GitHub Actions installs the locked dependencies and runs the frontend and backend checks independently on Linux.

The release does not distribute the maintainer's local `.env`, credentials, account database, saved games, private image store, original source-image masters, personal replay scripts or competitor research reports. The small rendered public demo assets are included. Software and example asset scopes are described in [NOTICE.md](../NOTICE.md).

Previous acceptance on the live local installation also verified ordinary-account denial from analytics, session renewal, real UI event ingestion and exports. The 213 existing checkpoints and 56 world-line head pointers remained unchanged. Publication and screenshot capture did not generate new paid supplier requests.
