# Architecture Constraints

Run `bpsai-pair arch check <path>` before completing any code task. Fix all errors before marking done.

## File Limits

Caps are project config, not fixed values -- the source of truth is the
`architecture:` block in `.paircoder/config.yaml` (`max_file_lines`,
`warning_file_lines`, `max_function_lines`, `max_functions_per_file`,
`max_imports`, plus `test_overrides` for test-file caps and
`role_overrides`/`profile_assignments` for per-path caps). Run
`bpsai-pair arch check <path>` to see the live numbers for whatever you're
checking rather than relying on a static table here.

## Key Rules

- `arch check` counts ALL functions in a file, including module-level helpers
- Extract helpers to a separate module to fix function count violations
- Run from project root: `bpsai-pair arch check <path/to/file.py>`
- Use the `architecting-modules` skill for decomposition guidance
