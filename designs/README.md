# Design artifact policy

This directory keeps reproducible design references and final competition evidence,
not generated search populations or optimizer memory.

## Protected final artifacts

- `osifog_finalization/` — the final submitted rocket, validation report, images,
  OpenEarth export, and submission parameters. Keep intact.
- `osifog_submission/` — the candidate comparison set and its reports. Keep intact
  as the audit trail behind the final selection.

## Retained references

- `osifog_level3/` — canonical best candidates, compact elite summaries, experiment
  evidence, and technical reports.
- `osifog_visuals/` — current visual candidates and render reports.

## Generated files

Campaign `campaign_ckg.json` files and their temporary snapshots are intentionally
excluded. They are optimizer working memory, can grow to multiple gigabytes, and
are not required to open or verify the final `.ork` design.
