# Equipments Clone

Python/FastAPI recreation of `AgenticFunProject/equipments`, built only from
the source repository's markdown documentation and Gherkin feature files.

The original service is TypeScript/Fastify. This clone intentionally uses a
different language and framework: Python 3.12 with FastAPI.

## Run

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/uvicorn equipments_clone.main:create_app --factory --host 0.0.0.0 --port 3000
```

Health is public:

```bash
curl http://localhost:3000/health
```

Protected routes require HS256 bearer JWTs. Local defaults match the gathered
contract:

- `AUTH_JWT_ISSUER=platform-auth`
- `AUTH_JWT_AUDIENCE=equipments-service`
- `AUTH_JWT_SECRET=equipments-dev-secret`

In development mode, `POST /dev/generate-token` creates local tokens for the
playground and tests.

## Test

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/ruff check .
```

The repository includes executable Gherkin-style contract coverage under
`tests/features/`, backed by `pytest-bdd` step definitions:

```bash
.venv/bin/pytest tests/test_gherkin_contract.py
```

## Persistence

Default storage is transient memory. SQLite-like durability is available via:

```bash
STORAGE_BACKEND=sqlite STORAGE_SQLITE_PATH=.data/equipments.sqlite \
  .venv/bin/uvicorn equipments_clone.main:create_app --factory
```

`STORAGE_SQLITE_EMPTY_ON_FIRST_BOOT=true` starts a new SQLite database without
seed data.
