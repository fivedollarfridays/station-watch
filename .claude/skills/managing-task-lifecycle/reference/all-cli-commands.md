# PairCoder CLI Complete Reference

> **This file is generated. Do not edit by hand.**
> Regenerate with: `python tools/cli/scripts/generate_cli_reference.py`
> Source of truth: the installed `bpsai-pair` Typer app (`bpsai_pair.cli:app`)
> bpsai-pair 2.49.9 | 340 commands across 54 groups

---

## Contents

- [Top-Level Commands](#top-level-commands)
- [Advisory Commands](#advisory-commands)
- [Arch Commands](#arch-commands)
- [Audit Commands](#audit-commands)
- [Benchmark Commands](#benchmark-commands)
- [Budget Commands](#budget-commands)
- [Cache Commands](#cache-commands)
- [Calibration Commands](#calibration-commands)
- [Channel Commands](#channel-commands)
- [Close-On-Merge Commands](#close-on-merge-commands)
- [Compaction Commands](#compaction-commands)
- [Config Commands](#config-commands)
- [Containment Commands](#containment-commands)
- [Enforce Commands](#enforce-commands)
- [Feedback Commands](#feedback-commands)
- [Fleet Commands](#fleet-commands)
- [Gaps Commands](#gaps-commands)
- [GH Commands](#gh-commands)
- [Github Commands](#github-commands)
- [Intent Commands](#intent-commands)
- [Issue Commands](#issue-commands)
- [License Commands](#license-commands)
- [MCP Commands](#mcp-commands)
- [Memory Commands](#memory-commands)
- [Metrics Commands](#metrics-commands)
- [Migrate Commands](#migrate-commands)
- [Orchestrate Commands](#orchestrate-commands)
- [Plan Commands](#plan-commands)
- [PM Commands](#pm-commands)
- [PR Commands](#pr-commands)
- [Preset Commands](#preset-commands)
- [QA Commands](#qa-commands)
- [QC Commands](#qc-commands)
- [Query Commands](#query-commands)
- [Release Commands](#release-commands)
- [Review Commands](#review-commands)
- [Security Commands](#security-commands)
- [Session Commands](#session-commands)
- [Setup Commands](#setup-commands)
- [Skill Commands](#skill-commands)
- [Sprint Commands](#sprint-commands)
- [Standup Commands](#standup-commands)
- [State Commands](#state-commands)
- [Subagent Commands](#subagent-commands)
- [Subscription Commands](#subscription-commands)
- [Support Commands](#support-commands)
- [System Commands](#system-commands)
- [Task Commands](#task-commands)
- [Telemetry Commands](#telemetry-commands)
- [Template Commands](#template-commands)
- [Timer Commands](#timer-commands)
- [Trello Commands](#trello-commands)
- [Ttask Commands](#ttask-commands)
- [Workspace Commands](#workspace-commands)

---

## Command Groups Overview

| Group | Commands |
|-------|----------|
| Top-Level | 17 |
| Advisory | 1 |
| Arch | 11 |
| Audit | 5 |
| Benchmark | 8 |
| Budget | 4 |
| Cache | 3 |
| Calibration | 4 |
| Channel | 2 |
| Close-On-Merge | 1 |
| Compaction | 5 |
| Config | 9 |
| Containment | 4 |
| Enforce | 3 |
| Feedback | 4 |
| Fleet | 15 |
| Gaps | 4 |
| GH | 4 |
| Github | 8 |
| Intent | 3 |
| Issue | 1 |
| License | 10 |
| MCP | 3 |
| Memory | 3 |
| Metrics | 9 |
| Migrate | 1 |
| Orchestrate | 8 |
| Plan | 12 |
| PM | 20 |
| PR | 3 |
| Preset | 3 |
| QA | 4 |
| QC | 4 |
| Query | 6 |
| Release | 17 |
| Review | 9 |
| Security | 5 |
| Session | 3 |
| Setup | 1 |
| Skill | 9 |
| Sprint | 2 |
| Standup | 2 |
| State | 8 |
| Subagent | 1 |
| Subscription | 2 |
| Support | 4 |
| System | 1 |
| Task | 17 |
| Telemetry | 13 |
| Template | 2 |
| Timer | 5 |
| Trello | 18 |
| Ttask | 9 |
| Workspace | 10 |
| **Total** | **340** |

---

## Top-Level Commands

| Command | Description | Options |
|---------|-------------|---------|
| `ci --json` | Run local CI checks (cross-platform). | `--json` |
| `contained-auto [task] --skip-checkpoint --yes --channels` | Start a contained autonomous session. | `--skip-checkpoint`, `--yes`, `--channels` |
| `context-sync --overall --last --next --blockers --json --auto --quiet --redact --append-next` | Update the Context Loop in /context/state.md. | `--overall`, `--last`, `--next`, `--blockers`, `--json`, `--auto`, `--quiet`, `--redact`, `--append-next` |
| `doctor --json --fix --acknowledge-editable --ack-ttl-days` | Run holistic health check on your PairCoder environment. | `--json`, `--fix`, `--acknowledge-editable`, `--ack-ttl-days` |
| `engage [backlog] [lane] --sprint --dry-run --show-tasks --json --max-parallel --skip-planning --resume --resume-run --create-branch --branch --reuse-branch --reclaim-remote-branch --no-kickoff-pr --preserve-failed-branch --provider --strict-targets --target-repo --tasks --reason --no-worktree --allow-extra-run --allow-unverified-ac --contract-lint-override --delete --age-hours --gone --task-branch --execute --merge-target --allow-unreachable-delete --stale-worktrees --clean --no-draft` | Parse a backlog and autonomously execute the sprint. | `--sprint`, `--dry-run`, `--show-tasks`, `--json`, `--max-parallel`, `--skip-planning`, `--resume`, `--resume-run`, `--create-branch`, `--branch`, `--reuse-branch`, `--reclaim-remote-branch`, `--no-kickoff-pr`, `--preserve-failed-branch`, `--provider`, `--strict-targets`, `--target-repo`, `--tasks`, `--reason`, `--no-worktree`, `--allow-extra-run`, `--allow-unverified-ac`, `--contract-lint-override`, `--delete`, `--age-hours`, `--gone`, `--task-branch`, `--execute`, `--merge-target`, `--allow-unreachable-delete`, `--stale-worktrees`, `--clean`, `--no-draft` |
| `feature <name> --primary --phase --force --type` | Create feature branch and scaffold context (cross-platform). | `--primary`, `--phase`, `--force`, `--type` |
| `init [template] --interactive --preset --name --goal --no-seeds` | Initialize repo with governance, context, prompts, scripts, and workflows. | `--interactive`, `--preset`, `--name`, `--goal`, `--no-seeds` |
| `pack --out --extra --dry-run --list --lite --json` | Create agent context package (cross-platform). | `--out`, `--extra`, `--dry-run`, `--list`, `--lite`, `--json` |
| `preflight [path] --quick --verbose` | Run tests with CI-matching environment. | `--quick`, `--verbose` |
| `prime-learn [insight] --no-push --category --yes --reject` | Capture an insight, synthesize it, and append to prime-knowledge YAML. | `--no-push`, `--category`, `--yes`, `--reject` |
| `scan-deps [path] --fail-on --verbose --json --no-cache` | Scan dependencies for vulnerabilities (shortcut for 'security scan-deps'). | `--fail-on`, `--verbose`, `--json`, `--no-cache` |
| `scan-secrets [path] --staged --diff --verbose --json` | Scan for secrets and credentials (shortcut for 'security scan-secrets'). | `--staged`, `--diff`, `--verbose`, `--json` |
| `status --json` | Show current context loop status and recent changes. | `--json` |
| `sweep --since --staged --working --json --category --confidence --fix --deep` | Sweep for dead code in recent changes. | `--since`, `--staged`, `--working`, `--json`, `--category`, `--confidence`, `--fix`, `--deep` |
| `upgrade --dry-run --skills --agents --commands --docs --config --force --auto --commit --no-seeds` | Upgrade existing v2.x project with latest content | `--dry-run`, `--skills`, `--agents`, `--commands`, `--docs`, `--config`, `--force`, `--auto`, `--commit`, `--no-seeds` |
| `validate --fix --json` | Validate repo structure and context consistency. | `--fix`, `--json` |
| `wizard --port --no-browser --demo --force --edit` | Setup wizard commands | `--port`, `--no-browser`, `--demo`, `--force`, `--edit` |

---

## Advisory Commands

| Command | Description | Options |
|---------|-------------|---------|
| `advisory scan [input_file] --package --repo --json --fail-on-impact` | Scan fleet repos against an advisory's affected-package list. | `--package`, `--repo`, `--json`, `--fail-on-impact` |

---

## Arch Commands

| Command | Description | Options |
|---------|-------------|---------|
| `arch check [path] --staged --fix --strict` | Check architecture constraints. | `--staged`, `--fix`, `--strict` |
| `arch check-config-readers [path] --strict --generate-baseline` | Flag direct ``yaml.safe_load``/``yaml.load`` reads of the config file, bypassing... | `--strict`, `--generate-baseline` |
| `arch check-encoding [path] --strict` | Flag text-mode file I/O that omits an explicit ``encoding=``. | `--strict` |
| `arch check-handler-length [path] --strict --generate-baseline` | Flag @app.command/@app.callback handlers over the configured line cap... | `--strict`, `--generate-baseline` |
| `arch check-model-ids [path] --strict` | Flag hardcoded ``claude-*`` model-id literals outside the registry (RE.6). | `--strict` |
| `arch check-provenance [path] --strict --generate-baseline` | Flag task-ID/security-finding/issue-PR/reviewer-attribution references baked into comments and... | `--strict`, `--generate-baseline` |
| `arch check-scale-readers [path] --strict` | Flag ``normalize_complexity(...)``/``complexity_band(...)`` calls whose scale argument is a... | `--strict` |
| `arch check-subprocess [path] --strict` | Flag raw subprocess.run/Popen/etc. | `--strict` |
| `arch check-wiring [path] --strict --generate-baseline` | Flag public symbols/modules written but never wired in. | `--strict`, `--generate-baseline` |
| `arch headroom [path] --threshold --strict` | Report files APPROACHING their architecture caps. | `--threshold`, `--strict` |
| `arch suggest-split <file_path>` | Suggest how to split a large file into smaller modules. |  |

---

## Audit Commands

| Command | Description | Options |
|---------|-------------|---------|
| `audit bypasses --days --limit --type --json` | Show recent workflow bypasses. | `--days`, `--limit`, `--type`, `--json` |
| `audit clear --yes` | Clear bypass log (for development/testing only). | `--yes` |
| `audit public-repo-tracked --json` | CI-runnable guard: REFUSE when this repo's remote is public and doctrine-tracked paths... | `--json` |
| `audit summary --days` | Show bypass summary by type and command. | `--days` |
| `audit tier-consistency --json --strict` | Audit license tier/feature-category consistency. | `--json`, `--strict` |

---

## Benchmark Commands

| Command | Description | Options |
|---------|-------------|---------|
| `benchmark check-packaging-governance` | The benchmark battery ships separately as the Model-Console add-on and is not installed. |  |
| `benchmark compare` | The benchmark battery ships separately as the Model-Console add-on and is not installed. |  |
| `benchmark list` | The benchmark battery ships separately as the Model-Console add-on and is not installed. |  |
| `benchmark matrix-regrade` | The benchmark battery ships separately as the Model-Console add-on and is not installed. |  |
| `benchmark matrix-run` | The benchmark battery ships separately as the Model-Console add-on and is not installed. |  |
| `benchmark results` | The benchmark battery ships separately as the Model-Console add-on and is not installed. |  |
| `benchmark run` | The benchmark battery ships separately as the Model-Console add-on and is not installed. |  |
| `benchmark seed <path> --run-id --json` | Seed local calibration from a benchmark matrix.csv. | `--run-id`, `--json` |

---

## Budget Commands

| Command | Description | Options |
|---------|-------------|---------|
| `budget check <task_id> --threshold --model --json` | Pre-flight budget check for a task. | `--threshold`, `--model`, `--json` |
| `budget estimate [task_id] --file --model --json` | Estimate token usage for a task or files. | `--file`, `--model`, `--json` |
| `budget programmatic --by --json` | Report month-to-date programmatic API spend vs. | `--by`, `--json` |
| `budget status --model --json` | Show current session budget status. | `--model`, `--json` |

---

## Cache Commands

| Command | Description | Options |
|---------|-------------|---------|
| `cache clear --confirm` | Clear the context cache. | `--confirm` |
| `cache invalidate <file_path>` | Invalidate cache for a specific file. |  |
| `cache stats --json` | Show cache statistics. | `--json` |

---

## Calibration Commands

| Command | Description | Options |
|---------|-------------|---------|
| `calibration derive-cx-seam --corpus --write --json` | Recompute the cx_seam percentile table from a corpus glob. | `--corpus`, `--write`, `--json` |
| `calibration pin --write --delete --force --json` | Show current effective per-task-type token estimates with provenance, and optionally pin them as... | `--write`, `--delete`, `--force`, `--json` |
| `calibration recommend-model --task-type --complexity --complexity-scale --cross-module --json` | Recommend a plan-time ``model:`` for a task (MR3.2). | `--task-type`, `--complexity`, `--complexity-scale`, `--cross-module`, `--json` |
| `calibration report --json` | Recompute calibration and report per task_type x model stats + drift. | `--json` |

---

## Channel Commands

| Command | Description | Options |
|---------|-------------|---------|
| `channel install <channel> --repo` | Install the latest RC wheel from dev CI into `.venv-experimental`. | `--repo` |
| `channel status` | Classify the stable and experimental installs found on disk. |  |

---

## Close-On-Merge Commands

| Command | Description | Options |
|---------|-------------|---------|
| `close-on-merge run --pr-body --pr-url --merge-sha --dry-run` | Parse *pr_body* for closing keywords and close each referenced issue with a disposition comment. | `--pr-body`, `--pr-url`, `--merge-sha`, `--dry-run` |

---

## Compaction Commands

| Command | Description | Options |
|---------|-------------|---------|
| `compaction check` | Check if compaction recently occurred. |  |
| `compaction cleanup --keep` | Remove old compaction snapshots. | `--keep` |
| `compaction recover` | Recover context after compaction. |  |
| `compaction snapshot list` | List available compaction snapshots. |  |
| `compaction snapshot save --trigger --reason --quiet` | Save a compaction snapshot with current context. | `--trigger`, `--reason`, `--quiet` |

---

## Config Commands

| Command | Description | Options |
|---------|-------------|---------|
| `config prefs get <key>` | Get a user preference value. |  |
| `config prefs list` | List all user preferences. |  |
| `config prefs set <key> <value>` | Set a user preference. |  |
| `config provider [name] --list` | Print env-var presets for LLM providers (HE Path A) | `--list` |
| `config set-gate <gate> <mode> --reason --until` | Set a governance gate's enforcement mode. | `--reason`, `--until` |
| `config show [section]` | Show current config or a specific section. |  |
| `config show-gates` | List every governance gate's resolved mode, plus strict-AC's and the merge guard's (the... |  |
| `config update --preset --dry-run` | Update config with missing sections from preset. | `--preset`, `--dry-run` |
| `config validate --preset --json` | Validate config against preset template. | `--preset`, `--json` |

---

## Containment Commands

| Command | Description | Options |
|---------|-------------|---------|
| `containment cleanup --keep` | Remove old containment checkpoints. | `--keep` |
| `containment list` | List containment checkpoints. |  |
| `containment rollback [checkpoint] --dry-run --force --pop-stash` | Rollback to a containment checkpoint. | `--dry-run`, `--force`, `--pop-stash` |
| `containment status --json` | Show containment configuration and active session status. | `--json` |

---

## Enforce Commands

| Command | Description | Options |
|---------|-------------|---------|
| `enforce containment --file --operation` | Enforce containment tiers for PreToolUse hook. | `--file`, `--operation` |
| `enforce state-edit --file --new-content` | Enforce state.md edit rules for PreToolUse hook. | `--file`, `--new-content` |
| `enforce task-edit --file --new-content --old-content` | Enforce task edit rules for PreToolUse hook. | `--file`, `--new-content`, `--old-content` |

---

## Feedback Commands

| Command | Description | Options |
|---------|-------------|---------|
| `feedback accuracy --days --json` | Compare estimated vs actual performance. | `--days`, `--json` |
| `feedback calibrate --json` | Trigger recalibration from telemetry data. | `--json` |
| `feedback query <task_type> --json` | Get estimates for a specific task type. | `--json` |
| `feedback status --json` | Show calibration health and statistics. | `--json` |

---

## Fleet Commands

| Command | Description | Options |
|---------|-------------|---------|
| `fleet audit --branches --issues --public-tracked --gates --json` | Report merged-residue branches, unclosed-but-referenced issues, (opt-in) public-repo... | `--branches`, `--issues`, `--public-tracked`, `--gates`, `--json` |
| `fleet audit-workflow-md` | Report repos with a stack-inappropriate or diverged workflow.md. |  |
| `fleet census [repo_path] --registry --format --out` | Census REPO_PATH's governed estate: walk it, classify each path group against the registry, and... | `--registry`, `--format`, `--out` |
| `fleet census-consumers <path_pattern> --roots` | Grep-verify real consumers of PATH_PATTERN: named file:line hits, never a guess from a... | `--roots` |
| `fleet census-disposition <report_path>` | Turn a committed census report into a per-class disposition- candidate table... |  |
| `fleet census-prune <history_path> --older-than --registry --apply --yes-i-checked-the-dry-run` | Retention for HISTORY_PATH's non-audit-trail run logs. | `--older-than`, `--registry`, `--apply`, `--yes-i-checked-the-dry-run` |
| `fleet check --upgrade --commit --init-missing --skip-member --reason --push --pr --cli-env --json --target-version` | Check bpsai-pair version compliance across workspace repos. | `--upgrade`, `--commit`, `--init-missing`, `--skip-member`, `--reason`, `--push`, `--pr`, `--cli-env`, `--json`, `--target-version` |
| `fleet config-rehearsal --projects-root --workspaces-file` | Fleet dry-run rehearsal: the same computation `pc upgrade --dry-run` uses, against every... | `--projects-root`, `--workspaces-file` |
| `fleet inventory --root --json` | Scan a projects root for `.venv`s with bpsai-pair installed. | `--root`, `--json` |
| `fleet land --skip-member --reason --target-version --json` | Merge each member's open `chore/upgrade-bpsai-pair-<version>` PR through the sanctioned merge... | `--skip-member`, `--reason`, `--target-version`, `--json` |
| `fleet propagate-settings --apply --dry-run --projects-root --workspaces-file` | `fleet propagate-settings` -- the sanctioned vehicle for re-propagating `.claude/settings.json`... | `--apply`, `--dry-run`, `--projects-root`, `--workspaces-file` |
| `fleet reconcile <member>` | Merge origin/<current-branch> into a diverged member, then push. |  |
| `fleet verify --target --root --json` | Exit non-zero when any in-workspace member is below `--target`. | `--target`, `--root`, `--json` |
| `fleet verify-agent-config --projects-root --workspaces-file` | Report-only `.claude/settings.json` hash-drift across workspace fleet members vs. | `--projects-root`, `--workspaces-file` |
| `fleet verify-payload --root --report-only --json` | Hash each fleet member's `.claude/` payload against the release-pinned manifest and exit non-zero... | `--root`, `--report-only`, `--json` |

---

## Gaps Commands

| Command | Description | Options |
|---------|-------------|---------|
| `gaps check <gap_id> --json` | Check quality gates for a specific gap. | `--json` |
| `gaps detect --json --analyze --with-gates` | Detect and classify all gaps from session history. | `--json`, `--analyze`, `--with-gates` |
| `gaps list --type --json` | List all classified gaps. | `--type`, `--json` |
| `gaps show <gap_id>` | Show detailed classification for a specific gap. |  |

---

## GH Commands

| Command | Description | Options |
|---------|-------------|---------|
| `gh issue comment` | _(no help text)_ |  |
| `gh issue create` | _(no help text)_ |  |
| `gh pr comment` | _(no help text)_ |  |
| `gh pr create` | _(no help text)_ |  |

---

## Github Commands

| Command | Description | Options |
|---------|-------------|---------|
| `github archive-merged [pr_number] --all --limit` | Archive tasks whose PRs have been merged. | `--all`, `--limit` |
| `github auto-pr --draft` | Auto-create PR for current branch if it has a task ID. | `--draft` |
| `github create --task --summary --draft --base` | Create a PR for a task. | `--task`, `--summary`, `--draft`, `--base` |
| `github link <task_id> --pr` | Link a task to a PR (update PR title). | `--pr` |
| `github list --state --task-only --json` | List pull requests. | `--state`, `--task-only`, `--json` |
| `github merge <pr_number> --method --no-delete --auto-next` | Merge a PR and optionally assign next task. | `--method`, `--no-delete`, `--auto-next` |
| `github pr [pr_number] --json` | Show PR status for current branch or specific PR. | `--json` |
| `github status --json` | Check GitHub connection status. | `--json` |

---

## Intent Commands

| Command | Description | Options |
|---------|-------------|---------|
| `intent detect <text> --json` | Detect work intent from text. | `--json` |
| `intent should-plan <text> --json` | Check if text should trigger planning mode. | `--json` |
| `intent suggest-flow <text>` | Suggest appropriate flow for text. |  |

---

## Issue Commands

| Command | Description | Options |
|---------|-------------|---------|
| `issue epic-sync --apply --repo --since --json` | Tick closed-but-unticked epic checklist items and close epics whose children are all closed --... | `--apply`, `--repo`, `--since`, `--json` |

---

## License Commands

| Command | Description | Options |
|---------|-------------|---------|
| `license activate --name --json` | Activate this machine on your license. | `--name`, `--json` |
| `license clear-cache --json` | Clear cached license validation data. | `--json` |
| `license deactivate --machine-id --yes --json` | Deactivate a machine from your license. | `--machine-id`, `--yes`, `--json` |
| `license features --json` | List available features for current tier. | `--json` |
| `license install <license_file> --force --activate --no-activate` | Install a license file to ~/.paircoder/license.json. | `--force`, `--activate`, `--no-activate` |
| `license machine-id --full --json` | Display this machine's unique identifier. | `--full`, `--json` |
| `license machines --json --full` | List all machines activated on your license. | `--json`, `--full` |
| `license path --json` | Show license file location. | `--json` |
| `license status --json` | Show current license status. | `--json` |
| `license verify-key --api-url --json` | Verify CLI public key matches API's keypair. | `--api-url`, `--json` |

---

## MCP Commands

| Command | Description | Options |
|---------|-------------|---------|
| `mcp serve --transport --port --verbose` | Start MCP server for Claude and other MCP-compatible agents. | `--transport`, `--port`, `--verbose` |
| `mcp test <tool> [input_json] --json` | Test an MCP tool locally. | `--json` |
| `mcp tools --json` | List available MCP tools. | `--json` |

---

## Memory Commands

| Command | Description | Options |
|---------|-------------|---------|
| `memory promote <memory_file> --bucket --allow-external-source` | Queue MEMORY_FILE for review. | `--bucket`, `--allow-external-source` |
| `memory ratify <candidate_id>` | Ratify a queued candidate into its bucket's shipping path. |  |
| `memory untrack --repo --fleet --apply --dry-run --projects-root` | Report (default) or remove tracked `.claude/agent-memory/**` from git. | `--repo`, `--fleet`, `--apply`, `--dry-run`, `--projects-root` |

---

## Metrics Commands

| Command | Description | Options |
|---------|-------------|---------|
| `metrics accuracy --json` | Show estimation accuracy report. | `--json` |
| `metrics breakdown --by --json` | Show cost breakdown by dimension. | `--by`, `--json` |
| `metrics budget --json` | Show budget status. | `--json` |
| `metrics burndown --sprint --start --end --json` | Generate burndown chart data for a sprint. | `--sprint`, `--start`, `--end`, `--json` |
| `metrics export --output --format` | Export metrics to file. | `--output`, `--format` |
| `metrics summary --json` | Show metrics summary from telemetry data. | `--json` |
| `metrics task <task_id> --json` | Show metrics for a specific task. | `--json` |
| `metrics tokens --json` | Show token estimation accuracy report. | `--json` |
| `metrics velocity --weeks --json` | Show velocity metrics for project planning. | `--weeks`, `--json` |

---

## Migrate Commands

| Command | Description | Options |
|---------|-------------|---------|
| `migrate status` | Show current PairCoder version status. |  |

---

## Orchestrate Commands

| Command | Description | Options |
|---------|-------------|---------|
| `orchestrate analyze <task_id> --json` | Analyze a task and show routing decision. | `--json` |
| `orchestrate auto-run [task_id] --plan --pr --test --json` | Run autonomous workflow for a single task. | `--plan`, `--pr`, `--test`, `--json` |
| `orchestrate auto-session --plan --max --pr --json` | Run autonomous session processing multiple tasks. | `--plan`, `--max`, `--pr`, `--json` |
| `orchestrate evaluate <event> --agent-id --agent-type --quiet` | Evaluate orchestration stop conditions; outputs JSON for CC hooks. | `--agent-id`, `--agent-type`, `--quiet` |
| `orchestrate handoff <task_id> --to --summary --out` | Create a handoff package for another agent. | `--to`, `--summary`, `--out` |
| `orchestrate select-agent <task_id> --prefer --json` | Select the best specialized agent for a task. | `--prefer`, `--json` |
| `orchestrate task <task_id> --prefer --max-cost --dry-run --json` | Orchestrate a task to the best agent. | `--prefer`, `--max-cost`, `--dry-run`, `--json` |
| `orchestrate workflow-status --json` | Show current autonomous workflow status. | `--json` |

---

## Plan Commands

| Command | Description | Options |
|---------|-------------|---------|
| `plan add-task <plan_id> --id --title --type --priority --complexity --sprint --force --reason` | Add a task to a plan. | `--id`, `--title`, `--type`, `--priority`, `--complexity`, `--sprint`, `--force`, `--reason` |
| `plan complete <plan_id> --force` | Mark a plan as complete. | `--force` |
| `plan ensure-from-backlog <backlog> --plan-id --contract-lint-override --reason` | Ensure a plan record exists for a drafted backlog (idempotent). | `--plan-id`, `--contract-lint-override`, `--reason` |
| `plan estimate <plan_id> --threshold --json --show-tasks` | Estimate token usage for a plan and suggest batching if needed. | `--threshold`, `--json`, `--show-tasks` |
| `plan feasibility <plan_id> --task --json --override` | Evaluate the five-term feasibility envelope for a plan (or one task). | `--task`, `--json`, `--override` |
| `plan list --status --json` | List all plans. | `--status`, `--json` |
| `plan new <slug> --type --title --skill --flow --goal --scope --feature-id --total-cx --reason` | Create a new plan. | `--type`, `--title`, `--skill`, `--flow`, `--goal`, `--scope`, `--feature-id`, `--total-cx`, `--reason` |
| `plan show <plan_id> --json` | Show details of a specific plan. | `--json` |
| `plan status [plan_id] --verbose --json` | Show plan status with sprint/task breakdown. | `--verbose`, `--json` |
| `plan sync-pm <plan_id> --board --target-list --create-lists --link --apply-defaults --only-new --fire-ready --dry-run --json` | Sync plan tasks to PM provider (or Trello directly). | `--board`, `--target-list`, `--create-lists`, `--link`, `--apply-defaults`, `--only-new`, `--fire-ready`, `--dry-run`, `--json` |
| `plan sync-trello <plan_id> --board --target-list --create-lists --link --apply-defaults --only-new --fire-ready --dry-run --json` | Sync plan tasks to PM provider (or Trello directly). (DEPRECATED) | `--board`, `--target-list`, `--create-lists`, `--link`, `--apply-defaults`, `--only-new`, `--fire-ready`, `--dry-run`, `--json` |
| `plan tasks <plan_id> --status --json` | List tasks for a specific plan. | `--status`, `--json` |

---

## PM Commands

| Command | Description | Options |
|---------|-------------|---------|
| `pm action <action_name> <item_id>` | Execute a button action on a work item. |  |
| `pm block <item_id> --reason` | Block a work item with a reason. | `--reason` |
| `pm check <item_id> <checklist_item_id> --uncheck` | Check or uncheck a checklist item. | `--uncheck` |
| `pm children <parent_id>` | List children of a work item. |  |
| `pm comment <item_id> <message>` | Add a comment to a work item. |  |
| `pm config` | Show resolved PM workflow configuration. |  |
| `pm create --type --title --parent` | Create a new work item via the PM provider. | `--type`, `--title`, `--parent` |
| `pm diagnostics` | Run PM provider diagnostics. |  |
| `pm done <item_id> --summary --strict` | Complete a work item (move to done). | `--summary`, `--strict` |
| `pm link <parent_id> <child_ids>` | Link child items to a parent (set parent-child relationship). |  |
| `pm migrate --dry-run` | Migrate from task-centric to backlog-centric PM structure. | `--dry-run` |
| `pm move <item_id> --status` | Move a work item to a new status. | `--status` |
| `pm set-field <item_id> --field --value` | Set a custom field on a work item. | `--field`, `--value` |
| `pm sprint complete <plan_id> --carry-forward` | Complete a sprint — evaluate tasks and produce summary. | `--carry-forward` |
| `pm sprint start <plan_id>` | Start a sprint (mark plan as in-progress). |  |
| `pm start <item_id>` | Start a work item (move to in_progress). |  |
| `pm status` | Show PM provider connection status. |  |
| `pm sync` | Sync local tasks to the PM provider. |  |
| `pm tree <root_id>` | Display hierarchy tree of a work item. |  |
| `pm unlink <child_id>` | Remove parent relationship from an item. |  |

---

## PR Commands

| Command | Description | Options |
|---------|-------------|---------|
| `pr merge-guard-check --pr --bot --freshness-seconds --json` | Decide whether a `gh pr merge` may proceed on the current branch. | `--pr`, `--bot`, `--freshness-seconds`, `--json` |
| `pr merge-ready <pr> --bot --freshness-seconds --json` | Read-only three-state merge predicate for a PR's current head. | `--bot`, `--freshness-seconds`, `--json` |
| `pr request-review --pr --budget --bot --json` | Request a review for a PR's current head, or refuse and report why. | `--pr`, `--budget`, `--bot`, `--json` |

---

## Preset Commands

| Command | Description | Options |
|---------|-------------|---------|
| `preset list --json` | List available configuration presets. | `--json` |
| `preset preview <name> --name --goal` | Preview the config.yaml that would be generated. | `--name`, `--goal` |
| `preset show <name> --json` | Show details for a specific preset. | `--json` |

---

## QA Commands

| Command | Description | Options |
|---------|-------------|---------|
| `qa init` | [DEPRECATED] Use 'bpsai-pair qc init'. |  |
| `qa list --tags --json` | [DEPRECATED] Use 'bpsai-pair qc list'. | `--tags`, `--json` |
| `qa report --json` | [DEPRECATED] Use 'bpsai-pair qc report'. | `--json` |
| `qa validate --json` | [DEPRECATED] Use 'bpsai-pair qc validate'. | `--json` |

---

## QC Commands

| Command | Description | Options |
|---------|-------------|---------|
| `qc init` | Initialize QC directory structure with example config and suite. |  |
| `qc list --tags --json` | List discovered QC test suites. | `--tags`, `--json` |
| `qc report --json` | Display results from the last QC run. | `--json` |
| `qc validate --json` | Validate all QC suite specs. | `--json` |

---

## Query Commands

| Command | Description | Options |
|---------|-------------|---------|
| `query metrics <name> --task-type --days --json` | Query metrics (success rates, estimation accuracy, agent performance). | `--task-type`, `--days`, `--json` |
| `query qc-trends --suite --env --json` | Query QC historical trends and flaky scenarios. | `--suite`, `--env`, `--json` |
| `query skill <name> --format --root` | Execute a query skill and output results. | `--format`, `--root` |
| `query state <key> --json` | Query project state (active tasks, current plan). | `--json` |
| `query task-state --task-id --plan-id --state --json` | Query task state transitions (pending → in_progress → done). | `--task-id`, `--plan-id`, `--state`, `--json` |
| `query tasks --status --json` | Query tasks with optional status filter. | `--status`, `--json` |

---

## Release Commands

| Command | Description | Options |
|---------|-------------|---------|
| `release checklist` | Show the release preparation checklist. |  |
| `release lint-changelog <path> --max-words` | Fail closed if *path* carries register/internal-ID/first-person/ exclamation content, or a bullet... | `--max-words` |
| `release pin-payload --check --version` | Generate (or --check) the release-pinned canonical `.claude` payload manifest that gates per-repo... | `--check`, `--version` |
| `release plan --sprint --version --create` | Generate release preparation tasks. | `--sprint`, `--version`, `--create` |
| `release prep --since --create-tasks --skip-tests` | Verify release readiness and generate tasks for missing items. | `--since`, `--create-tasks`, `--skip-tests` |
| `release record-docs-drift` | Record the Phase 8.5 docs-drift report. |  |
| `release record-phase8 --website-repo` | Record Phase 8 (website) completion. | `--website-repo` |
| `release record-phase9 --root --version` | Record Phase 9 (fleet) completion. | `--root`, `--version` |
| `release tag --version --push --message --bypass --reason` | Fail closed unless the Phase 8 (website), docs-drift, and dogfood gates all pass for this... | `--version`, `--push`, `--message`, `--bypass`, `--reason` |
| `release validate-versions --fix --strict` | Check version consistency across config files. | `--fix`, `--strict` |
| `release verify-docs-drift --bypass --reason` | Fail closed if the Phase 8.5 docs-drift report artifact is missing or stale relative to the... | `--bypass`, `--reason` |
| `release verify-dogfood --version --wheelhouse` | Fail closed unless the dogfood battery (tools/cli/tests/dogfood) passes against the BUILT WHEEL,... | `--version`, `--wheelhouse` |
| `release verify-dogfood-skew --version --wheelhouse --previous-tag --payload-ref` | Fail closed unless the `gh pr merge` guard stays intelligible with the CLI and the `.claude`... | `--version`, `--wheelhouse`, `--previous-tag`, `--payload-ref` |
| `release verify-notes --website-repo` | Fail closed if the release's public notes (website changelog, GH release body, README What's New)... | `--website-repo` |
| `release verify-phase8` | Fail closed if the Phase 8 (website) completion artifact is missing or stale relative to the... |  |
| `release verify-phase9` | Fail closed if the Phase 9 (fleet) completion artifact is missing or stale relative to the... |  |
| `release verify-tiers --previous-tag --version` | Fail closed if a license tier loses a feature key -- or a retired key ships undocumented --... | `--previous-tag`, `--version` |

---

## Review Commands

| Command | Description | Options |
|---------|-------------|---------|
| `review auto [query] --json` | Auto-route a review request to the correct subcommand. | `--json` |
| `review bot-gate --pr --bot --wait-seconds --request-rereview --json` | Gate a merge on zero unanswered bot root threads on the current head. | `--pr`, `--bot`, `--wait-seconds`, `--request-rereview`, `--json` |
| `review branch --base --json` | Review the current branch diff against base (pre-PR validation). | `--base`, `--json` |
| `review checks-gate --pr --target-branch --wait-seconds --override-reason --json` | Gate a merge on target-branch CI (bound to its current tip) and the PR's own head check-runs... | `--pr`, `--target-branch`, `--wait-seconds`, `--override-reason`, `--json` |
| `review harvest --org --since --out --until --regime-cutover --exclude-repo` | Harvest review-cycle data for an org into JSONL finding/pr rows. | `--org`, `--since`, `--out`, `--until`, `--regime-cutover`, `--exclude-repo` |
| `review notify-wake --pr` | Drop an early-wake signal for a waiting ``review bot-gate`` instance. | `--pr` |
| `review pr <number> --post --publish-status --adversarial-verify --json` | Review a GitHub PR by number. | `--post`, `--publish-status`, `--adversarial-verify`, `--json` |
| `review sprint <sprint_id> --publish-status --json` | Fleet review for a cross-repo sprint: review every touched repo's PR, audit cross-repo contracts,... | `--publish-status`, `--json` |
| `review task [task_id] --json` | Review a single task's changes. | `--json` |

---

## Security Commands

| Command | Description | Options |
|---------|-------------|---------|
| `security install-hook --overwrite` | Install pre-commit hook for secret scanning. | `--overwrite` |
| `security pre-commit --json` | Run secret scan as a pre-commit hook. | `--json` |
| `security reconcile-permission-floor [path] --json` | Repair a permission-floor prune sealed by an orphaned receipt. | `--json` |
| `security scan-deps [path] --fail-on --verbose --json --no-cache` | Scan dependencies for known vulnerabilities. | `--fail-on`, `--verbose`, `--json`, `--no-cache` |
| `security scan-secrets [path] --staged --diff --verbose --json` | Scan for secrets and credentials in code. | `--staged`, `--diff`, `--verbose`, `--json` |

---

## Session Commands

| Command | Description | Options |
|---------|-------------|---------|
| `session check --force --quiet` | Check session state and display context if new session. | `--force`, `--quiet` |
| `session set-role <role>` | Set the active agent role for status line display. |  |
| `session status --budget` | Show current session status including token budget. | `--budget` |

---

## Setup Commands

| Command | Description | Options |
|---------|-------------|---------|
| `setup statusline --theme --remove --preview` | Configure the PairCoder status line for Claude Code. | `--theme`, `--remove`, `--preview` |

---

## Skill Commands

| Command | Description | Options |
|---------|-------------|---------|
| `skill export [skill_name] --format --all --dry-run` | Export skills to other AI coding tool formats. | `--format`, `--all`, `--dry-run` |
| `skill gaps --json --clear --analyze` | List detected skill gaps from session history. | `--json`, `--clear`, `--analyze` |
| `skill generate [gap_id] --auto-approve --overwrite --preview` | Generate a skill from a detected gap. | `--auto-approve`, `--overwrite`, `--preview` |
| `skill install <source> --project --personal --name --overwrite` | Install a skill from URL or local path. | `--project`, `--personal`, `--name`, `--overwrite` |
| `skill list --json` | List all skills in .claude/skills/. | `--json` |
| `skill recommend --top --format --verbose` | Recommend skills based on intelligence signals. | `--top`, `--format`, `--verbose` |
| `skill score [skill_name] --json` | Score skills on quality dimensions. | `--json` |
| `skill suggest --json --create --min` | Analyze session history and suggest new skills. | `--json`, `--create`, `--min` |
| `skill validate [skill_name] --fix --json --no-security` | Validate skills against Anthropic specs. | `--fix`, `--json`, `--no-security` |

---

## Sprint Commands

| Command | Description | Options |
|---------|-------------|---------|
| `sprint complete <sprint_id> --skip-checklist --reason --plan --archive` | Complete a sprint with checklist verification and optional archival. | `--skip-checklist`, `--reason`, `--plan`, `--archive` |
| `sprint list --plan` | List sprints in a plan. | `--plan` |

---

## Standup Commands

| Command | Description | Options |
|---------|-------------|---------|
| `standup generate --plan --since --format --output` | Generate a daily standup summary. | `--plan`, `--since`, `--format`, `--output` |
| `standup post --plan --since` | Post standup summary to Trello board's Notes list. | `--plan`, `--since` |

---

## State Commands

| Command | Description | Options |
|---------|-------------|---------|
| `state advance <task_id> <to_state> --reason` | Manually advance task to a new state. | `--reason` |
| `state archive --keep --older-than --head-older-than --rotate-head --dry-run --check --json --auto` | Archive completed 'What Was Just Done' entries (and superseded '> Prior:' header claims) out of... | `--keep`, `--older-than`, `--head-older-than`, `--rotate-head`, `--dry-run`, `--check`, `--json`, `--auto` |
| `state fence --json` | Lint state.md's managed fence for duplicate section headings. | `--json` |
| `state history [task_id] --limit` | Show state transition history. | `--limit` |
| `state list --status` | List all tracked task states. | `--status` |
| `state reset <task_id> --yes` | Reset a task to NOT_STARTED state. | `--yes` |
| `state show <task_id>` | Show current execution state for a task. |  |
| `state validate --quiet --task --full` | Validate that tasks marked done in state.md are actually complete. | `--quiet`, `--task`, `--full` |

---

## Subagent Commands

| Command | Description | Options |
|---------|-------------|---------|
| `subagent gaps --json --clear --analyze` | List detected subagent gaps from session history. | `--json`, `--clear`, `--analyze` |

---

## Subscription Commands

| Command | Description | Options |
|---------|-------------|---------|
| `subscription manage --no-browser --return-url --json` | Open the Stripe Billing Portal to manage your subscription. | `--no-browser`, `--return-url`, `--json` |
| `subscription status --json` | Show your subscription status. | `--json` |

---

## Support Commands

| Command | Description | Options |
|---------|-------------|---------|
| `support create --type --title --description --json` | Create a support ticket with auto-attached system info. | `--type`, `--title`, `--description`, `--json` |
| `support list --status --json` | List your support tickets. | `--status`, `--json` |
| `support open --no-browser --verbose` | Open support portal with auto-login. | `--no-browser`, `--verbose` |
| `support show <ticket_id> --json` | Show a specific support ticket with comments. | `--json` |

---

## System Commands

| Command | Description | Options |
|---------|-------------|---------|
| `system info --json` | Show local environment info (version, Python, OS) for bug reports. | `--json` |

---

## Task Commands

| Command | Description | Options |
|---------|-------------|---------|
| `task ac <task_id> --plan` | Show acceptance criteria status for a task. | `--plan` |
| `task archive [task_ids] --completed --sprint --plan --version --no-changelog --dry-run` | Archive completed tasks. | `--completed`, `--sprint`, `--plan`, `--version`, `--no-changelog`, `--dry-run` |
| `task auto-next --plan` | Automatically assign and start the next pending task. | `--plan` |
| `task changelog-preview --sprint --plan --version` | Preview changelog entry for tasks. | `--sprint`, `--plan`, `--version` |
| `task check <task_id> [item_text] --uncheck --plan` | Check or uncheck acceptance criteria items. | `--uncheck`, `--plan` |
| `task check-terminal-provenance --project-root` | Refuse to commit a staged task file that introduces an unprovenanced terminal ``status:... | `--project-root` |
| `task cleanup --retention --dry-run` | Clean up old archived tasks. | `--retention`, `--dry-run` |
| `task done <task_id> --plan --no-hooks` | Mark a task as done (updates task file + runs consistency check). | `--plan`, `--no-hooks` |
| `task lint-corpus --orchestrator-root --target-root` | Report task ids present in both roots whose complexity/plan/status disagree. | `--orchestrator-root`, `--target-root` |
| `task list --plan --status --json` | List tasks. | `--plan`, `--status`, `--json` |
| `task list-archived --plan --json` | List archived tasks. | `--plan`, `--json` |
| `task materialize <backlog> --tasks --plan-id --sprint --contract-lint-override --reason` | Materialize task files (and the plan record) from a backlog. | `--tasks`, `--plan-id`, `--sprint`, `--contract-lint-override`, `--reason` |
| `task next --start` | Show the next task to work on. | `--start` |
| `task reconcile --from-merged --base --dry-run` | Batch-reconcile stale `in_progress` task files against merged lane history. | `--from-merged`, `--base`, `--dry-run` |
| `task restore <task_id> --plan` | Restore a task from archive. | `--plan` |
| `task show <task_id> --plan` | Show details of a specific task. | `--plan` |
| `task update <task_id> --status --plan --no-hooks --skip-state-check --resync --local-only --reason --strict --force-local --auto-check --allow-dirty --role --conflicts-encountered --bypass-conflicts-gate --conflicts-gate-bypass-reason` | Update a task's status. | `--status`, `--plan`, `--no-hooks`, `--skip-state-check`, `--resync`, `--local-only`, `--reason`, `--strict`, `--force-local`, `--auto-check`, `--allow-dirty`, `--role`, `--conflicts-encountered`, `--bypass-conflicts-gate`, `--conflicts-gate-bypass-reason` |

---

## Telemetry Commands

| Command | Description | Options |
|---------|-------------|---------|
| `telemetry aggregate --workspace --format --output --since --include-garbage --json` | Aggregate telemetry across all repos in a workspace. | `--workspace`, `--format`, `--output`, `--since`, `--include-garbage`, `--json` |
| `telemetry archive-pre-t1 --json` | Archive pre-T1 telemetry and restart the audit hash chain. | `--json` |
| `telemetry backfill-failure-taxonomy --force` | Seed signals.jsonl with the run-exit failure-mode taxonomy's backfill corpus -- a hand-curated,... | `--force` |
| `telemetry backfill-interventions` | One-time backfill: join the existing ``intervention_events.jsonl`` onto existing... |  |
| `telemetry config --enable --disable --privacy --retention --json` | Configure telemetry settings. | `--enable`, `--disable`, `--privacy`, `--retention`, `--json` |
| `telemetry coverage --days --json` | Report per-field population, population mix, and configured-vs-observed privacy. | `--days`, `--json` |
| `telemetry export --format --output --since --until --task-type --anonymize` | Export telemetry data for analysis or backup. | `--format`, `--output`, `--since`, `--until`, `--task-type`, `--anonymize` |
| `telemetry intervention record --kind --task-id --detail --metadata --json` | Record one chat-plane intervention event. | `--kind`, `--task-id`, `--detail`, `--metadata`, `--json` |
| `telemetry log-failure --trigger --quiet` | Log an API failure signal to telemetry. | `--trigger`, `--quiet` |
| `telemetry log-session-end --outcome-summary --stop-reason --session-id --quiet` | Log a session_end (info) signal. | `--outcome-summary`, `--stop-reason`, `--session-id`, `--quiet` |
| `telemetry log-subagent-outcome --stop-reason --agent-id --agent-type --session-id --quiet` | Log a subagent_outcome (info) signal. | `--stop-reason`, `--agent-id`, `--agent-type`, `--session-id`, `--quiet` |
| `telemetry recover <archive_dir> --dry-run --project` | Recover historical telemetry from Claude session archives. | `--dry-run`, `--project` |
| `telemetry status --json` | Show telemetry collection status and statistics. | `--json` |

---

## Template Commands

| Command | Description | Options |
|---------|-------------|---------|
| `template check --fail-on-drift --fix --verbose` | Check for drift between source files and cookie cutter template. | `--fail-on-drift`, `--fix`, `--verbose` |
| `template list` | List files tracked for template sync. |  |

---

## Timer Commands

| Command | Description | Options |
|---------|-------------|---------|
| `timer show <task_id> --json` | Show time entries for a task. | `--json` |
| `timer start <task_id> --description` | Start a timer for a task. | `--description` |
| `timer status` | Show current timer status. |  |
| `timer stop` | Stop the current timer. |  |
| `timer summary --plan --json` | Show time summary across tasks. | `--plan`, `--json` |

---

## Trello Commands

| Command | Description | Options |
|---------|-------------|---------|
| `trello apply-defaults <card_id>` | Apply project default values to a Trello card. |  |
| `trello boards --json` | List available Trello boards. | `--json` |
| `trello config --show --set-list --set-field --agent` | View or modify Trello configuration. | `--show`, `--set-list`, `--set-field`, `--agent` |
| `trello connect --api-key --token` | Connect to Trello (validates and stores credentials). | `--api-key`, `--token` |
| `trello disconnect` | Remove stored Trello credentials. |  |
| `trello fields --board --refresh --json` | Show custom fields and their valid options for a board. | `--board`, `--refresh`, `--json` |
| `trello init-board --name --from-template --keep-cards --set-active` | Create a new Trello board from a template. | `--name`, `--from-template`, `--keep-cards`, `--set-active` |
| `trello list-fields` | List all custom fields on the active board (table format). |  |
| `trello lists` | Show lists on the active board. |  |
| `trello progress <task_id> [message] --blocked --waiting --step --started --completed --review --agent` | Post a progress comment to a Trello card. | `--blocked`, `--waiting`, `--step`, `--started`, `--completed`, `--review`, `--agent` |
| `trello set-field <card_id> --project --stack --status --effort --repo-url --field --value` | Set custom field values on a Trello card. | `--project`, `--stack`, `--status`, `--effort`, `--repo-url`, `--field`, `--value` |
| `trello status` | Check Trello connection status. |  |
| `trello sync --from-trello --preview --list` | Sync tasks between Trello and local files. | `--from-trello`, `--preview`, `--list` |
| `trello use-board <board_id>` | Set the active Trello board for this project. |  |
| `trello webhook delete <webhook_id>` | Delete a registered webhook. |  |
| `trello webhook list` | List all registered webhooks for the current token. |  |
| `trello webhook register <callback_url> --board` | Register a webhook with Trello. | `--board` |
| `trello webhook serve --host --port --agent --auto-assign --verbose` | Start the Trello webhook server with agent assignment. | `--host`, `--port`, `--agent`, `--auto-assign`, `--verbose` |

---

## Ttask Commands

| Command | Description | Options |
|---------|-------------|---------|
| `ttask block <card_id> --reason` | Mark a task as blocked. | `--reason` |
| `ttask check <task_id> <item_text> --checklist` | Check off a checklist item as complete. | `--checklist` |
| `ttask comment <task_id> <message>` | Add a progress comment to a task. |  |
| `ttask done <card_id> --summary --list --auto-check --strict --allow-dirty --role --conflicts-encountered --bypass-conflicts-gate --conflicts-gate-bypass-reason` | Complete a task (moves to Done list). | `--summary`, `--list`, `--auto-check`, `--strict`, `--allow-dirty`, `--role`, `--conflicts-encountered`, `--bypass-conflicts-gate`, `--conflicts-gate-bypass-reason` |
| `ttask list --list --agent --status` | List tasks from Trello board. | `--list`, `--agent`, `--status` |
| `ttask move <card_id> --list` | Move a task to a different list. | `--list` |
| `ttask show <card_id>` | Show task details from Trello. |  |
| `ttask start <card_id> --summary --budget-override` | Start working on a task (moves to In Progress). | `--summary`, `--budget-override` |
| `ttask uncheck <task_id> <item_text> --checklist` | Uncheck a checklist item (mark as incomplete). | `--checklist` |

---

## Workspace Commands

| Command | Description | Options |
|---------|-------------|---------|
| `workspace audit <project> --fix --json` | Run a full audit of a sibling project. | `--fix`, `--json` |
| `workspace check-impact --since --json` | Check contract changes and their cross-repo impact. | `--since`, `--json` |
| `workspace index --topic --json` | Query the workspace document index by topic. | `--topic`, `--json` |
| `workspace init --name --projects --scan --no-scan --force --json` | Initialize a new workspace configuration. | `--name`, `--projects`, `--scan`, `--no-scan`, `--force`, `--json` |
| `workspace init-project [name] --all --force --dry-run --json --no-claude` | Initialize a project in the workspace with PairCoder scaffolding. | `--all`, `--force`, `--dry-run`, `--json`, `--no-claude` |
| `workspace pull [project] --rebase --json` | Pull latest changes from remote for workspace projects. | `--rebase`, `--json` |
| `workspace repos <workspace_id> --all --json` | List the repos declared by the covering workspace config. | `--all`, `--json` |
| `workspace setup-permissions --dry-run --force --json --adopt-legacy` | Generate .claude/settings.local.json from workspace config. | `--dry-run`, `--force`, `--json`, `--adopt-legacy` |
| `workspace status --json` | Show workspace configuration and project states. | `--json` |
| `workspace validate --repo --json` | Validate the covering workspace config (CI-usable). | `--repo`, `--json` |
