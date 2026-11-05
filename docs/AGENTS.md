# Documentation directory

Use `docs/` for maintained, long-term project documentation: architecture,
operating procedures, policies, and reference material.

Do not put generated reports, HTML review pages, previews, run receipts,
experiments, or other intermediate agent output here. Use the repository-root
`.scratch/` directory instead; `.scratch/reports/` is the default for review
reports. This directory is ignored by Git and must remain untracked. Operational
run artifacts may continue to use the existing ignored `artifacts/` directory.

Keep reusable GitHub report templates and GitHub automation assets in `.github/`.
When sharing a private preview, serve a file from an ignored directory. Do not
copy it into `docs/` to make it accessible, and never put private Tailscale URLs
in GitHub issues, comments, or pull request descriptions.

## Current guides and historical reference

`README.md` is the project entry point and `docs/README.md` is the task index.
Each current operation, economic policy and API contract has one owning guide.
Update it in the same change as the behavior it describes. Link related topics
instead of copying policies or writing another implementation diary.

Curated, dated design/rollout/measurement records may be retained under
`docs/archive/` as historical reference. They must state their captured revision,
limits and current successor. Frozen raw benchmark evidence belongs under
`benchmarks/results/` with its hash/provenance. These are reviewed reference
assets, not permission to commit new generated runs or operational receipts.
Fresh output still belongs in ignored `.scratch/` or `artifacts/`.

Keep `.github/weekly-sync-warning-report.md` at its approved packaged path with
its approval semantics intact. Keep fixture provenance next to its fixtures.
Do not put live deployment IDs or temporary ports into current setup defaults.
Do not infer that an archived release remains retained after consolidation.
