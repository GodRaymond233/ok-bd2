# PVP recognition fixtures

`recent_pvp_home_fhd.png` is a privacy-minimized 1920×1080 regression frame
derived from the 2026-08-12 live home capture. Only the exact recent-cartridge
template bounding box `(1659, 935, 94, 82)` is retained; every other pixel is
black. It verifies the real rendered PVP cover rather than a synthetic copy of
the template asset.

`pvp_hub_top_bar_shifted_fhd.png` (BUG-20260905-08, RPT-20260905-195025) is the
same kind of strip-only canvas for the live PVP-hub top bar whose whole bar sat
about 4 px higher than calibration. Only the medal-template search strip
`(793, 29, 340, 55)` is retained.
