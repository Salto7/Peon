# Legacy skills catalog

Engagement agents on the CrewAI branch use **`roles/`** (ROLE.yaml) via
`RoleRegistry` / `RoleRouter`. Jobs store `role_ids`, not skill names.

This `skills/` tree remains for **Learn / authoring** and the catalog browser
(`SkillRegistry`). Do not wire new Job execution or planning through skills.
