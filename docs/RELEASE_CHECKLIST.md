# Release Checklist

Use this before tagging a release or handing the app to a non-developer.
**"Production" for Prospectly means a local, single-user install on loopback**
(`docs/PRODUCT_BRIEF.md` §4, `docs/TECHNOLOGY_DECISIONS.md` D1–D14). Hosted
multi-user deployment is deferred to Phase 6 and has additional requirements
that this list does not cover.

---

## 1. Scope and freeze

- [ ] Release is scoped to one milestone or one coherent change series.
- [ ] Working tree is clean; no debug files, screenshots, `.env`, `data/`,
      `backups/`, `logs/` or `uploads/` are staged.
- [ ] `git log --oneline <last-tag>..HEAD` contains only intended commits.
- [ ] Product invariants still hold: no send path, human approval, P5/P6
      enums unchanged (`AGENTS.md` §1).

## 2. Quality gate

Run from a clean checkout with the project virtualenv:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest
```

- [ ] All four commands pass, with 0 failures.
- [ ] Test count in `docs/QA_CHECKLIST.md` and
      `docs/design/pipeline-canvas.md` matches the real suite.
- [ ] No new `TODO`/`FIXME` in `app/`.

Dependency audit (network required):

```bash
uvx pip-audit
```

- [ ] `No known vulnerabilities found`.
- [ ] Record the audit date in the release notes.

## 3. Database and fresh-install check

Migrations must work from empty and from the previous release.

```bash
# Empty database
rm -f /tmp/prospectly-release.db
DATABASE_URL=sqlite:////tmp/prospectly-release.db uv run alembic upgrade head

# Previous release database (use a copy, never the live file)
cp data/prospectly.db /tmp/prospectly-upgrade.db
DATABASE_URL=sqlite:////tmp/prospectly-upgrade.db uv run alembic upgrade head
```

- [ ] Fresh migration reaches `head` without errors.
- [ ] Upgrade migration on a copy of the previous release DB succeeds.
- [ ] The app boots against the fresh DB:

```bash
DATABASE_URL=sqlite:////tmp/prospectly-release.db \
APP_ACCESS_TOKEN=release-smoke \
uv run uvicorn app.main:app --host 127.0.0.1 --port 8766
```

- [ ] `/healthz` returns 200.
- [ ] `/login` returns 200; gated `/prospects` redirects; gated `/api/docs`
      returns 401.
- [ ] `/api/openapi.json` loads after login and the version matches the release.
- [ ] `python -m app.cli worker --once` exits 0 on an empty queue.

## 4. Backup and restore drill

```bash
uv run python -m app.cli backup
ls -lt backups/ | head
```

- [ ] A new snapshot exists, is non-zero, and opens with
      `sqlite3 backups/<file> 'PRAGMA integrity_check;'` → `ok`.
- [ ] Restore has been rehearsed at least once: copy the snapshot to a
      temporary path, point `DATABASE_URL` at it, boot the app, and open a
      prospect.
- [ ] `deploy/systemd/prospectly-backup.timer` is enabled on the machine that
      will run the app (or an equivalent scheduled job exists).

## 5. Documentation consistency

- [ ] Version is identical in `pyproject.toml`, `app/config.py`,
      `docs/openapi.json`, and the README status line.
- [ ] `uv run python -m app.cli export-openapi` was run after API changes, and
      `git diff --exit-code docs/openapi.json` is clean.
- [ ] `docs/API_DESIGN.md`, `docs/DATABASE_DESIGN.md`,
      `docs/SYSTEM_ARCHITECTURE.md`, `docs/UI_UX_SPECIFICATION.md` and
      `docs/USER_FLOWS.md` match the shipped behaviour (AGENTS §9).
- [ ] `docs/SECURITY_AND_PRIVACY.md` §9 abuse cases are all checked and backed
      by named tests.
- [ ] `docs/QA_CHECKLIST.md` has an entry for this pass with the real test
      count and any known gaps.
- [ ] No contradictory status/count/version strings:

```bash
grep -rn "tests pass\|Test gate\|0\.1\.0\|0\.2\.0" README.md docs/
```

## 6. Release commit and tag

```bash
git add -A
git commit -m "release: vX.Y.Z"
git tag -a vX.Y.Z -m "Prospectly vX.Y.Z"
git push origin main
git push origin vX.Y.Z
```

- [ ] The tag points at the commit that passed the gate in §2.
- [ ] Release notes state: scope, known gaps, minimum Python version,
      whether a worker process is required, and the dependency-audit date.
- [ ] `git describe --tags --exact-match HEAD` prints the new tag.

## 7. Post-release smoke (on the machine that runs it)

- [ ] Start the app and worker the way the operator will
      (`deploy/systemd/README.md` or the README manual path).
- [ ] Add a synthetic prospect, run research with fake or real providers,
      generate a draft, approve it, open WhatsApp, confirm sent, record a
      reply — or walk the relevant flows in `docs/USER_FLOWS.md`.
- [ ] Confirm the backup timer fired and `logs/app.log` has no secret values.
- [ ] Delete the synthetic data or restore the pre-test backup.

## 8. Rollback

If the release is bad:

1. Stop the app and worker.
2. `git checkout <previous-tag>` (or deploy the previous commit).
3. Restore the database snapshot taken in §4.
4. Restart and verify `/healthz` plus one read/write flow.
5. Record the failure in `docs/QA_CHECKLIST.md` and add a regression test
   before re-releasing.
