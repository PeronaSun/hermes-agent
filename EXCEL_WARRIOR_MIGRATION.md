# Excel Warrior → Bagel migration (2026-07-22)

## Architecture and invariants

Bagel at `/Users/perona/.hermes/hermes-agent` remains the only Hermes runtime.
No gateway or Portal service was started. Existing Feishu identity, connection
configuration, local runtime configuration and Feishu Docx tools are untouched.
The source repository remains read-only. No credential file is part of this
migration.

The migrated code is isolated under `integrations/excel_warrior`, exposed by a
service-gated plugin under `plugins/excel_warrior`, and instructed by two
namespaced Skills under `skills/excel-warrior-*`. Existing in-progress Bagel
changes implement the deterministic Feishu meeting preview/confirm workflow;
they take routing precedence over the source Skill's obsolete direct-write
instruction.

## Capability matrix

| Capability | Source | Dependencies | Directly runnable | Hermes adaptation / conflict | Migration |
|---|---|---|---|---|---|
| Project search, preview, status/remark/meta writes | `skills/project-tracking-portal/scripts/portal_data_tool.py` | stdlib, portal JSON | Yes | Needs actor/reason and tool schema; complements Bagel Bitable workflow | Namespaced Skill + `excel_warrior_project` plugin tool |
| Project/maison create, update, confirmed cascade delete | same | stdlib, `fcntl` (macOS compatible) | Yes | Destructive calls retain source confirmation guard | Same tool, isolated data |
| Risk/progress/status/timeline and Meeting Records linkage | source Portal records plus current `tools/feishu_bitable_tool.py` | Feishu SDK at runtime | Yes with configured Feishu | Current Bagel implementation is more complete and deterministic | Preserve current Bagel workflow; local Portal remains query/audit mirror |
| Action Items with owner/deadline/status/priority/source meeting | current Bagel Feishu workflow; source minute scripts provide task/owner/due | Feishu SDK | Yes with configured Feishu | Source implementation lacks several fields and confirmation | Prefer current tested Bagel implementation; source archive supplements it |
| Meeting parsing/import from Feishu doc/wiki or local DOCX | `skills/meeting-minutes/scripts/feishu_doc_reader.py` | `python-docx` for local files | URL path yes; DOCX needs dependency | Must preserve existing Docx write tools | `excel_warrior_doc_reader`, existing tools untouched |
| Meeting wiki archive, summaries, decisions/content, project links | `meeting_minutes_tool.py`, `_wiki.py`, `_bitable.py` | Feishu access at runtime | Yes when configured | Source direct-write route conflicts with confirmation rule | Tool registered but Skill requires Bagel preview/confirmation first |
| Meeting idempotency and audit | generated IDs, file lock, changelog; current Bagel fingerprints | stdlib | Yes | Current Bagel fingerprinting is stronger | Both retained; Bagel workflow has routing precedence |
| Weekly report | `portal_data_tool.format_weekly_report` | stdlib | Yes | None | Native plugin action + Skill instructions |
| Feishu Base mirror/reconcile/backfill | `feishu_bitable.py`, `reconcile_from_base.py`, `backfill_from_base.py` | configured Feishu app | Offline logic yes; live sync not run | Must not read credentials or write real Feishu during tests | Scripts migrated; live calls remain explicit/manual |
| Audit log, locks, snapshots | `portal_data_tool.py`, `src/change_logger.py` | stdlib, SQLite | Yes | None | Migrated unchanged in isolated layer |
| Portal dashboard/matrix/project/maison/data-health views | `portal/` | browser + static HTTP server | Yes | No gateway integration required | Full static site, data and images migrated |
| Portal validation and selectors/statistics | `portal/src/*.js` | Node.js | Yes | None | Full source + tests migrated |
| Excel → JSON import and image extraction | `src/excel_to_json.py`, source workbook | stdlib | Yes | Hard-coded paths resolve inside isolated layer | Code and workbook migrated |
| Legacy HTML editor, image gallery, recalculation | `src/html_*`, `recalculate.py`, `build_html.py` | `beautifulsoup4` | Yes if dependency installed | Legacy path can conflict with JSON source of truth | Compatibility tool; current JSON tool is preferred |
| Standalone Qwen/OpenAI agents | `agents/qwen_agent.py`, `hermes_agent_with_html_tool.py` | `openai`; optional `langchain-openai` tests | Not in current base Python | Would create a second agent loop and conflict with single runtime | Archived under integration, not started; capabilities wrapped into Bagel tools |
| Hermes deployment overlays | `deployments/hermes/*` | old Hermes registry API | Not directly against current runtime | Old overlay would overwrite core and duplicate stale routes | Business behavior adopted via plugin; deploy/gateway scripts intentionally not copied |
| Shell deployment/rollback workflows | `deploy/` | bash, system paths | Source-specific | Could touch runtime config or gateway | Audited only; replaced by this manifest and backups |

## File mapping and rollback

| Source | Target | Reason | Rollback |
|---|---|---|---|
| `portal/`, `src/`, `agents/`, `source_data /` | `integrations/excel_warrior/` | Isolated capability/data layer | Remove the target directory |
| `skills/meeting-minutes/` | `skills/excel-warrior-meeting-minutes/` | Namespaced Skill; paths adapted | Remove the target directory |
| `skills/project-tracking-portal/` | `skills/excel-warrior-project-tracking-portal/` | Namespaced Skill; paths adapted | Remove the target directory |
| latest Hermes wrapper behavior | `plugins/excel_warrior/` | Current plugin API, no core tool footprint | Remove the target directory |
| migration regression tests | `tests/excel_warrior/` | Registration/routing/read-only validation | Remove the target directory |

All targets above were new at migration time, so no existing file required a
`.bak.20260722`. If any target is subsequently replaced, first copy it beside
itself with that suffix. The pre-existing full runtime backup remains at
`/Users/perona/.hermes/hermes-agent-backup-20260722`.

## Dependency delta

No dependency was installed and no unrelated version changed. Plugin-local
requirements are declared in `plugins/excel_warrior/plugin.yaml`; they are not
added to Bagel's always-installed core dependencies. Optional archived-agent
compatibility dependencies remain manual:

```bash
cd /Users/perona/.hermes/hermes-agent
venv/bin/python -m pip install 'beautifulsoup4>=4.12,<5' 'python-docx>=1.1,<2'
# Only if running archived standalone-agent compatibility tests:
venv/bin/python -m pip install langchain-openai
```

Only `python-docx` and `beautifulsoup4` are needed for user-facing migrated
DOCX/legacy HTML features. Bagel already declares `openai==2.24.0`;
`langchain-openai` is needed only for one archived standalone-agent test. On
macOS 12 with Python 3.11 the two plugin dependencies are pure Python.

Configuration variable names used by migrated live Feishu code are
`FEISHU_APP_ID`, `FEISHU_APP_SECRET`, and the existing Base app-token setting.
They mean app identity, app credential, and target Base identity respectively.
No value was read, copied, printed, or modified.

## Operations

Portal start (manual):

```bash
cd /Users/perona/.hermes/hermes-agent/integrations/excel_warrior/portal
python3 -m http.server 8765 --bind 127.0.0.1
```

Gateway restart after all verification:

```bash
cd /Users/perona/.hermes/hermes-agent
venv/bin/python hermes gateway restart
```

Rollback only this migration:

```bash
cd /Users/perona/.hermes/hermes-agent
mv integrations/excel_warrior /private/tmp/excel_warrior.rollback.20260722
mv plugins/excel_warrior /private/tmp/excel_warrior-plugin.rollback.20260722
mv skills/excel-warrior-meeting-minutes /private/tmp/excel-warrior-meeting-minutes.rollback.20260722
mv skills/excel-warrior-project-tracking-portal /private/tmp/excel-warrior-project-tracking-portal.rollback.20260722
mv tests/excel_warrior /private/tmp/excel_warrior-tests.rollback.20260722
```

No command above is executed automatically.

## Verification record

- Source Python suite: collection blocked for two standalone-agent files by
  missing `openai` in the source interpreter and missing `langchain-openai`;
  remaining suite: **341 passed, 3 failed**. All three failures are the same
  missing optional `python-docx` dependency; no live Feishu write was run.
- Source Portal baseline: **48 passed, 2 failed** because tests expected stale
  `Total Projects`/`Total Maisons` labels while the renderer emits
  `Projects`/`Maisons`.
- Migrated plugin unit tests: **4 passed**.
- Bagel plugin discovery, Skill utilities, Feishu Bitable and Feishu Docx
  regression selection: **151 passed**.
- Migrated Portal after correcting only those stale assertions: **50 passed**.
- `py_compile`: passed for the plugin, migrated business modules and Skill
  scripts.
- Process check: exactly one matching Hermes gateway process (PID 70275) was
  present; migration started neither a gateway nor a Portal process.

Live Feishu end-to-end test (manual, after restart): send meeting text with
Maison/project, decisions, risk, progress and action items; verify Bagel emits
one unified preview without a Change ID; reply only `确认`; verify one Meeting
Record, project status/risk/progress updates and de-duplicated Action Items;
repeat the same input and confirm no duplicate is written. Then ask for the
project overview and open the existing Feishu Docx read/write flow to verify it
remains available.
