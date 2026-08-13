# Trade shop recognition fixtures

These masked fixtures come from the user-provided 2026-08-12 trade recording.
Only the production relative shop item/status ROI is retained. Its 1920x1080
reference bounds `(360, 140)-(1620, 510)` scale to `(270, 105)-(1215, 383)`
in the recorded 1440x810 game client; every other pixel is black. Account,
desktop, notification, and Codex UI regions are excluded.

| Fixture | Source time | Expected `soled-out.png` result |
| --- | ---: | --- |
| `before_purchase.png` | 42.0s | Rejected before the purchase result appears. |
| `after_purchase.png` | 43.0s | Accepted after the sold-out labels appear. |
| `sell_page.png` | 61.5s | Rejected after switching to the sell page. |

The source recording was supplied by the repository owner for diagnosing and
regression-testing this automation flow.
