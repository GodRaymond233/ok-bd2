# Full-HD map-trade video regression fixtures

These fixtures are derived from a user-provided 1920×1080 tutorial video.
Each file preserves only the listed source ROI pixels; every remaining pixel
is black. They are intentionally small, deterministic recognition inputs rather
than playable screenshots.

The contributor who supplied the recording owns it and authorized its use for
this repository's recognition tests. No account, chat, notification, or system
UI is retained in these masked fixtures.

| Fixture | Approximate source timestamp | Retained 1920×1080 ROIs `(left, top, right, bottom)` | Expected state |
| --- | --- | --- | --- |
| `story_sandbox_ready.png` | 30.4s | minimap `(425, 360, 480, 430)`; absorb `(1490, 840, 1575, 915)`; summon `(1525, 735, 1625, 815)`; subdue `(1640, 690, 1725, 770)`; group switch `(1630, 970, 1790, 1055)` | Story sandbox confirms group 1; absorb, summon, and subdue are available. |
| `story_sandbox_used.png` | 33.7s | Same ROIs as `story_sandbox_ready.png`. | Story sandbox confirms group 1; absorb, summon, and subdue are used. |
| `teleport_map_multiple_enabled.png` | 25.5s | enabled circles `(780, 575, 890, 675)` and `(995, 275, 1105, 375)` | Exactly two enabled teleport candidates; the lower-left candidate near `(834, 625)` is selected. |
| `story_badge_05_encoded.png` | 47.0s | Q5 selector neighborhood `(1325, 905, 1385, 968)` | Full badge ranking identifies the encoded Q5 candidate; no Q6 selection is accepted. |

The Q5 source candidate deliberately falls just below the prior strict
pixel/margin acceptance gates, so it protects the intended tolerant production
behavior without encoding those implementation thresholds in the test.
