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

`sale_120_markers_fhd.png` is a privacy-minimized 2026-08-16 live sell-page
frame. Only three exact `52x14` rendered `↑120%` marker ROIs at
`(493,563)`, `(825,563)`, and `(1157,563)` are retained; every other pixel is
black. It regression-tests the real marker rendering without retaining the
shop page, account information, or surrounding game state.

The source recording was supplied by the repository owner for diagnosing and
regression-testing this automation flow.

`sale_native_2k_new.png` and `sale_native_2k_old.png` retain only the item-name
and marker rectangle `(580,200)-(950,280)` from two native 2560x1440 report
frames supplied on 2026-11-03. They reproduce the strict native pixel-score
miss and verify the 1080 reference-view recovery. All other pixels are black;
the report ZIPs and complete screenshots are not shipped.
