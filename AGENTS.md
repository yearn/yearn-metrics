# Agent instructions

Read [the testing policy](docs/testing.md) before adding, changing, or removing
tests. Keep distinct supported failure modes at the cheapest faithful boundary.
Consolidate exploratory tests before delivery; identify surviving coverage when
removing duplicates. Test count and coverage percentages are not goals.

Request `postgres_database` directly or through a fixture for database tests.
Do not bypass the database guard or make Postgres setup automatic for pure tests.
Keep CI execution policy unchanged unless the task explicitly authorizes changes.

When presenting completed changes, provide a working private preview or an HTML
review document using the `tailscale-preview` skill. Never include private
Tailscale URLs in GitHub comments, issues, or pull request descriptions.
