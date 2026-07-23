# excel_warrior capability layer

This directory is the isolated, single-runtime migration of
`/Users/perona/.hermes/excel_warrior`. It contains no gateway configuration and
starts no service automatically.

- `portal/`: static Portal and its JSON source of truth.
- `src/`: Excel import, HTML compatibility, recalculation, validation and
  changelog business modules.
- `agents/`: archived standalone agent entry points for reference and offline
  compatibility. They are not started; Bagel remains the only agent runtime.
- `source_data/`: the source workbook used by the import/rebuild utilities.

Runtime tools are registered by `plugins/excel_warrior`. Agent instructions
are registered by the two `skills/excel-warrior-*` directories.

Portal (manual start only):

```bash
cd /Users/perona/.hermes/hermes-agent/integrations/excel_warrior/portal
python3 -m http.server 8765 --bind 127.0.0.1
```

Open `http://127.0.0.1:8765/`. This is a static local service and is unrelated
to the Hermes gateway.
