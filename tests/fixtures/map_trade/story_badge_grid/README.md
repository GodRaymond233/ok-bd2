# Low-resolution story badge grid fixtures

These fixtures preserve only the quick-switch footer needed by the story badge
matcher; every pixel above that footer is blacked out.  They keep the original
client resolution so relative ROI and template-scale behavior remain realistic.

- `native_720_q6_visible.png`: local 1280x720 WGC frame with story badge 6 at
  the first complete slot.
- `native_720_q6_absent.png`: local 1280x720 WGC frame from the later viewport;
  badge 6 is not visible and must not be recovered.
- `native_864_q6_visible.png`: reported 1536x864 frame where the former strict
  full-badge gate rejected badge 6.

The source screenshots were supplied by the user on 2026-08-14.  Only the
bottom quick-switch UI is retained to keep the regression evidence bounded.
