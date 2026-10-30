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
