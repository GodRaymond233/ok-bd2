# Map page mode fixtures

These 1920x1080 PNGs are client-area crops derived from the three user-provided
map screenshots captured on 2026-08-13. The 32px Windows title bar and 1px side
borders were removed. Every unrelated region was blacked out; only the visual
signals needed by the map-page detector remain.

- `direct.png`: interaction-button direct teleport page.
- `generate.png`: skill-opened generation teleport page.
- `sandbox_large.png`: upper-left-button sandbox large map.

Expected modes are mutually exclusive: `direct_teleport`,
`generate_teleport`, and `sandbox_large_map` respectively.

- `direct_soft_1161.png`: native 2064×1161 report frame from 2026-10-26,
  retaining the direct header, arrows and map label after noninteger scaling.
- `generate_beginner_811.png`: native 1442×811 report frame from 2026-10-25,
  retaining the beginner talent header, arrows and map label. Unrelated regions
  are blacked out in both report fixtures.
