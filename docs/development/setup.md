# Local development setup

Use the interpreter pinned in `.python-version` and the committed `uv.lock`.
Run from this checkout; keep its environment separate from other worktrees.

```bash
uv sync --locked --extra dev
. .venv/bin/activate
python -c 'import yearn_data.cli; print(yearn_data.cli.__file__)'
```

The printed module must belong to this checkout. Do not install an unrelated
checkout into this environment to make older commands work. Services use their
own dedicated environment and explicit source path; see [nightly operations](../operations/nightly.md).

## Choose a local database before writing

The CLI normally loads repository dotenv files, and already exported environment
variables take precedence. Most commands default to `YEARN_DATA_DB` or `neon`.
The `refresh` command additionally requires an explicit `--db`. Never rely on a
default database while learning a workflow. `--db staging` must point to a
separate local database through `STAGING_DB_URL`; a missing URL is an error.
See [Postgres operations](../operations/postgres.md) for an existing staging setup.

For a new, disposable developer database, choose an unused localhost port and
container name. The following uses a port distinct from the test/staging units:

```bash
docker run --detach --name yearn-data-dev \
  --publish 127.0.0.1:55437:5432 \
  --env POSTGRES_USER=yearn_dev --env POSTGRES_PASSWORD=yearn_dev \
  --env POSTGRES_DB=yearn_dev postgres:18-alpine
export STAGING_DB_URL='postgresql://yearn_dev:yearn_dev@127.0.0.1:55437/yearn_dev?sslmode=disable'
```

These credentials are local examples. Check readiness and wait for this command
to succeed before initializing; do not reuse a production URL:

```bash
docker exec yearn-data-dev pg_isready -U yearn_dev -d yearn_dev
```

Then initialize the local schema, without collecting data:

```bash
yearn-data --db staging init-db
```
 Read [testing](testing.md) before provisioning the
separate disposable test database; do not use the developer container as an
implicit global test setup.

## Select a task

- [Research/export](../operations/research-exports.md): collect deliberately bounded history and export completed analyses.
- [Native refresh](../operations/refresh.md): prepare or publish a coherent multi-chain release on an explicitly chosen database.
- [Read-only API](../operations/hosted-api.md): serve already prepared data; staging is not preseeded by setup.
- [Testing policy](testing.md): offline checks, disposable Postgres and optional EVM replay.

Provider/RPC settings are described by `.env.example` and the owning operation.
Keep credentials in ignored environment files. Do not copy a production dotenv
file into a review or test checkout. Seed staging only through a separately
reviewed dump/restore; setup does not clone production automatically.

[Documentation index](../README.md).
