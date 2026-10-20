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

These three source screenshots were supplied by the user on 2026-08-14. Only the
bottom quick-switch UI is retained to keep the regression evidence bounded.

- `video_810_q16_visible.png`: the 40-second frame of the user's
  `2026-10-15 18-30-53.mp4`, cropped to the displayed game client at
  `(35, 45, 1440, 810)`. Pixels above row 630 are blacked out. Badge 16 is
  fully visible at `(1044, 704)` but its template can lose to 18; the real OCR
  and ordered row must agree before selection and again before clicking.
- `video_810_q20_at_end.png`: the 35-second frame of the user's
  `2026-10-15 19-57-01.mp4`, with the same client crop and footer mask.
  Chapter 20 at `(1329, 704)` has a fully visible cartridge body at the right
  scroll limit, while the wider completion-icon region extends past the client.
  Selection is allowed; collection completion remains unknown.
