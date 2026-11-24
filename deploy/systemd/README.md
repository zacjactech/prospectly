# Running Prospectly as a systemd user service (Linux)

These units run the web app, the background job worker, and a daily backup
timer as the **logged-in user** — no root, no Docker. They are optional: the
manual commands in the main README remain the supported path.

The templates contain `/path/to/prospectly` placeholders. Replace them with
the absolute path to this checkout before installing.

## Install

```bash
cd /path/to/prospectly

# 1. Make sure the virtualenv exists and the database is migrated.
uv sync
.venv/bin/python -m alembic upgrade head

# 2. Copy and point the units at this checkout.
mkdir -p ~/.config/systemd/user
cp deploy/systemd/*.service deploy/systemd/*.timer ~/.config/systemd/user/
sed -i "s|/path/to/prospectly|$PWD|g" ~/.config/systemd/user/prospectly-*.service
sed -i "s|/path/to/prospectly|$PWD|g" ~/.config/systemd/user/prospectly-*.timer

# 3. Load and start.
systemctl --user daemon-reload
systemctl --user enable --now prospectly-app.service prospectly-worker.service
systemctl --user enable --now prospectly-backup.timer

# 4. Keep user services running after logout / at boot.
loginctl enable-linger "$USER"
```

The app listens on `127.0.0.1:8000`. Edit `ExecStart` in
`~/.config/systemd/user/prospectly-app.service` to change host or port, then
run `systemctl --user daemon-reload && systemctl --user restart prospectly-app`.

## Verify

```bash
systemctl --user status prospectly-app prospectly-worker
systemctl --user list-timers prospectly-backup.timer
curl -fsS http://127.0.0.1:8000/healthz
journalctl --user -u prospectly-app -u prospectly-worker -n 50 --no-pager
```

The app also writes a rotating log to `logs/app.log` in the checkout.

Test the backup path once by hand:

```bash
systemctl --user start prospectly-backup.service
ls -lt backups/ | head
```

## Configuration

The app reads `.env` from the working directory (the checkout), so provider
keys and `APP_ACCESS_TOKEN` stay in that file — never in the unit files.
`UMask=0077` keeps the database, logs and backups readable by your user only.

The units bind to loopback, which matches the product's single-user model.
If you ever expose the app beyond loopback:

- set `APP_ACCESS_TOKEN` in `.env` (startup refuses a non-loopback bind without it);
- terminate TLS in a reverse proxy and change the hardcoded
  `secure=False` cookie flags in `app/auth.py` / `app/csrf.py`;
- add login rate limiting (see `docs/SECURITY_AND_PRIVACY.md` §3).

## Stop / remove

```bash
systemctl --user disable --now prospectly-app.service prospectly-worker.service
systemctl --user disable --now prospectly-backup.timer
rm ~/.config/systemd/user/prospectly-{app,worker,backup}.service \
   ~/.config/systemd/user/prospectly-backup.timer
systemctl --user daemon-reload
```
