# Changelog

## 2026-05-20

### Changed

- Updated the embedded dashboard visual style to align with the management-center palette, table treatment, status pills, rounded charts, and toast feedback for manual refresh actions.
- Added a collector watchdog for the unified `run` command so an unexpectedly exited collector target is restarted while the dashboard process remains alive.
- Removed an unused test import and cleaned ignored Python cache artifacts from the working tree.

### Security

- Redacted sensitive request fields before persisting `usage_events.raw_json`. The dashboard still stores `api_key_hash` for aggregation, but raw event JSON now replaces `api_key`, `authorization`, `access_token`, `refresh_token`, and `id_token` values with `[redacted]`.
- Stopped returning `quota_snapshots.raw_json` from the quota JSON API; the UI only receives the fields it renders.

### Validation

- Added regression coverage for raw event redaction, collector watchdog restart behavior, visual tokens, refresh toasts, quota status colors, date filtering, and period-based API summaries.
- Release checks for this branch should include unittest discovery, Python bytecode compilation, local API smoke tests, and the sensitive-pattern scan documented in `docs/deployment.md`.
