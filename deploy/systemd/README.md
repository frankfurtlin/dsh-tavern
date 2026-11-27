# systemd units

Deployment templates for the two services the Tavern application needs. They are
hand-written copies of the units the author runs, with this machine's absolute
paths replaced by the `/opt/dsh/<repo>` convention and secrets removed.

| File | Installs as | Service |
| --- | --- | --- |
| `dsh-tavern-api.service.example` | `/etc/systemd/system/dsh-tavern-api.service` | FastAPI app on `127.0.0.1:8001` |
| `dsh-tavern-web.service.example` | `/etc/systemd/system/dsh-tavern-web.service` | static client on `127.0.0.1:8810` |
| `legacy/dsh-tavern-worker.service.example` | not installed | old text worker — unused, do not enable |
| `legacy/dsh-tavern-world-worker.service.example` | not installed | old world worker — module deleted, cannot start |

`/opt/dsh/dsh-tavern` is an example path, not a requirement: after `git clone` to
wherever you keep checkouts, replace it in every `WorkingDirectory`,
`EnvironmentFile` and `ExecStart` line. `dsh-tavern-api` and `dsh-tavern-web` have
no ordering dependency on each other; the plugin proxies to both ports and treats
each one as optional (a dead port is a dead half of the panel, not a failed DSH).

## Install

```sh
sudo git clone https://github.com/frankfurtlin/dsh-tavern /opt/dsh/dsh-tavern
cd /opt/dsh/dsh-tavern

# 1) API environment and dependencies (Python 3.12)
python3.12 -m venv app/venv
app/venv/bin/pip install -r app/server/requirements.lock
python scripts/init-env.py                 # writes app/.env, mode 600, never overwrites
#   edit app/.env: DATABASE_URL -> your PostgreSQL, AI_MODE=dsh,
#   DSH_AGENT_URL=http://127.0.0.1:3080/tavern-agent/v1,
#   DSH_AGENT_SECRET == DSH_TAVERN_AGENT_SECRET, DSH_TRUSTED_SECRET (see the unit header)

# 2) schema (reads DATABASE_URL from app/.env through app.db)
cd app/server
set -a; . ../.env; set +a
../venv/bin/alembic upgrade head
cd ../..

# 3) client build (app/client/dist is a gitignored build artifact)
cd app/client && npm ci && npm run build && cd ../..

# 4) units
sudo cp deploy/systemd/dsh-tavern-api.service.example /etc/systemd/system/dsh-tavern-api.service
sudo cp deploy/systemd/dsh-tavern-web.service.example /etc/systemd/system/dsh-tavern-web.service
sudo systemctl daemon-reload
sudo systemctl enable --now dsh-tavern-api dsh-tavern-web

curl -s http://127.0.0.1:8001/health/ready
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8810/
```

Then install the plugin half (`dsh plugin add <path-to-your-checkout>`, or the
Plugins page), add `dsh-tavern` to `dsh.profile.bundles`, and restart DSH Web.
The panel drives the app through `/tavern/*`; the app calls the model through
`/tavern-agent/v1`, which the plugin serves.

## First run: the trusted account

The panel does not send a browser session: the plugin forwards an `x-dsh-trusted`
header with a shared secret, and the API maps that to one fixed account. Three
values in `app/.env` have to agree with the host:

```dotenv
DSH_TRUSTED_AUTH=true          # off by default; without it every panel request is 401
DSH_TRUSTED_USERNAME=<user>    # must already exist and be active in the app
DSH_TRUSTED_SECRET=<random>    # the plugin reads the same value from app/.env
```

`DSH_TRUSTED_USERNAME` is looked up, never created: with a secret mismatch or an
unknown/inactive name the API answers `TRUSTED_USER_UNAVAILABLE` (503) and the
panel stays empty. Register that account once (the app's own signup, with
`REGISTRATION_ENABLED=true`) before trusting the header, and keep the name stable.

Optional, only for the app's own provider mode: `../venv/bin/python -m app.cli
configure-dsh-model --username <same name>` (run from `app/server` with `app/.env`
exported) attaches a `dsh://agent` model profile. With `AI_MODE=dsh` generation goes
straight to the plugin gateway and needs no profile.

## Why the `.service.example` suffix

systemd only loads files whose name ends in exactly `.service` from its unit
search path (`/etc/systemd/system`, `/run/systemd/system`, `/usr/lib/systemd/system`).
A `*.service.example` file inside a git checkout is therefore inert: it is not
scanned, it cannot be pulled in by `systemctl enable`, and cloning the repo never
adds a unit. The copy step above is the only thing that activates one.

The plugin package does not ship units either: `dsh plugin add` symlinks the
checkout into the DSH profile and only `lib/`, `client.js` and
`cordis.patch.yml` are loaded — `deploy/` is not referenced by `package.json`,
`dsh.bundle.patch` or the client entry.

## `legacy/`

`dsh-tavern-worker` and `dsh-tavern-world-worker` exist on the author's host but
are disabled. Current code has no background queue (`app/worker.py` only holds
`adapter_for`) and the `app.world.worker` module was removed with the old story
domain, so the second one restart-loops if enabled. They live in `legacy/` so the
`deploy/systemd/*.example` glob in the install step above cannot pick them up.

## Verification

```sh
for u in api web; do
  sed 's#/opt/dsh/dsh-tavern#/your/checkout#g' \
    deploy/systemd/dsh-tavern-$u.service.example > /tmp/dsh-tavern-$u.service
  systemd-analyze verify /tmp/dsh-tavern-$u.service
done
```

`systemd-analyze verify` may still warn about host-specific dependencies (for
example a commented-out `postgresql.service` ordering) but must not report
syntax errors.

## User and permissions

The units ship `User=root` because that is what the author runs. For a production
host, run both services as a dedicated account:

```sh
sudo useradd --system --home /opt/dsh/dsh-tavern --shell /usr/sbin/nologin tavern
sudo chown -R tavern:tavern /opt/dsh/dsh-tavern/app/venv
sudo chown tavern:tavern /opt/dsh/dsh-tavern/app/.env      # mode 600
sudo chown -R tavern:tavern /path/from/STORAGE_PATH        # private uploads
# then set User=tavern / Group=tavern in both units
```

Secrets belong in `app/.env` (mode `600`, owned by the service user), never in the
unit files.
