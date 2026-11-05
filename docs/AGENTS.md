# Documentation directory

Use `docs/` for maintained, long-term project documentation: architecture,
operating procedures, policies, and reference material.

Store generated reports, HTML review pages, previews, run receipts, experiments and other
intermediate agent output in the repository-root `.scratch/` directory; `.scratch/reports/`
is the default for review
reports. This directory is ignored by Git and must remain untracked. Operational
run artifacts may continue to use the existing ignored `artifacts/` directory.

Keep reusable GitHub report templates and GitHub automation assets in `.github/`.
Serve private previews from ignored directories and share their URLs only in the private
review conversation. Keep GitHub issues, comments and pull request descriptions free of
private Tailscale URLs.

## Current guides and historical reference

`README.md` is the project entry point and `docs/README.md` is the task index.
Each current operation, economic policy and API contract has one owning guide.
Update it in the same change as the behavior it describes. Link related topics to their
owning guides.

Curated, dated design/rollout/measurement records may be retained under
`docs/archive/` as historical reference. They must state their captured revision,
limits and current successor. Frozen raw benchmark evidence belongs under
`benchmarks/results/` with its hash/provenance. These reviewed reference assets preserve
captured evidence. Store fresh generated runs and operational receipts in ignored
`.scratch/` or `artifacts/`.

Keep `.github/weekly-sync-warning-report.md` at its approved packaged path with
its approval semantics intact. Keep fixture provenance next to its fixtures.
Use reusable configuration in current setup defaults. Establish live deployment IDs, ports
and release retention through current operational checks.

## Language and framing

Describe each component by its role, behavior, outputs and evidence requirements.
Lead with affirmative definitions and concrete actions. Explain availability,
accounting scope and operating preconditions directly. Use comparisons when a
reader needs to choose between supported options. Preserve exact identifiers,
commands, quoted evidence and explicit authorization requirements.
