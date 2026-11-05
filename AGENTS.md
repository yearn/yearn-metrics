# Agent instructions

Read [the testing policy](docs/development/testing.md) before adding, changing, or removing
tests. Keep distinct supported failure modes at the cheapest faithful boundary.
Consolidate exploratory tests before delivery; identify surviving coverage when
removing duplicates. Choose coverage for the supported behavior and failure modes it verifies.

Request `postgres_database` directly or through a fixture for database tests.
Preserve the database guard and keep pure tests independent of Postgres setup.
Keep CI execution policy unchanged unless the task explicitly authorizes changes.

When presenting completed changes, provide a working private preview or an HTML
review document using the `tailscale-preview` skill. Share private Tailscale URLs only in
the private review conversation; keep GitHub comments, issues and pull request descriptions
free of those URLs.
