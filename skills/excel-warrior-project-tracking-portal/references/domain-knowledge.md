# Domain Knowledge

## Statistics Formulas

Column header statistics (shown per maison in the main table):
- **Total** = count of statuses matching `Q*` (Q1-Scale, Q2-Pilot, etc.) + `Ideation`
- **Ideation** = count of exact `Ideation`
- **Pilot** = count of statuses containing `Pilot`
- **Scale** = count of statuses containing `Scale`
- **Run** = count of exact `Run`
- **Done** = count of exact `Done`

Key insight: **Column Total excludes Done and Run** — only "in-progress" statuses (Ideation, Q*-prefixed) count toward the Total shown in headers.

Row statistics (G column in main table, per project):
- **Total** = count of ALL non-empty statuses across all maisons

## Maison Column Order (Sheet_1 / Main Table)

The main table columns (left to right after metadata):
1. Louis Vuitton
2. Christine Dior Couture
3. Loro Piana
4. Fendi
5. Celine
6. Loewe
7. Givenchy
8. Kenzo
9. Berluti
10. Marc Jocobs (note: original typo, not "Jacobs")
11. Rimowa
12. Tiffany
13. Bvlgari
14. Chaumet
15. Fred
16. Hublot
17. TAG Heuer
18. Zenith
19. Perfume Christine Dior (PCD)
20. Guerlain
21. Beauty Division
22. Sephora
23. MHD

## Maison Aliases

Common aliases the user may use:
- "LV" → Louis Vuitton (`louis_vuitton`)
- "Dior", "Dior Couture" → Christine Dior Couture (`christine_dior_couture`)
- "PCD", "Perfume Dior" → Perfume Christine Dior (`perfume_christine_dior`)
- "Beauty Div" → Beauty Division (`beauty_division`)

## Status Lifecycle

Typical project progression:
`Ideation` → `Q*-Pilot` → `Q*-Scale` → `Run` → `Done`

- `Q*-Pilot` / `Q*-Scale` = planned timeline (e.g., Q2-Pilot means pilot in Q2)
- `Run` = actively running
- `Done` = completed

## JSON Structure

`project_maison_status.json` entries:
```json
{
  "project_id": "snake_case_id",
  "maison_id": "snake_case_id",
  "status": "Ideation",
  "remark": "",
  "updated_at": "2026-06-16"
}
```

Only entries with non-empty status exist (no "empty" placeholder records).
