# Common image issues in the demo dataset (Milestone 1)

Observed on the 48 cover photos (24 students x Section A + Section B). Each issue lists
where it shows up and how the pipeline deals with it.

| Issue | Example scans | How it is handled |
|---|---|---|
| Two cameras: 3072x4096 (~8.5 MB) and 3472x4624 (~4.5 MB) | a001-a005 vs a006-a013 | Everything is warped onto one 300-dpi template canvas (2480x3508), so resolution no longer matters |
| Light floral cloth background - page edge cannot be found reliably | a001-a005, a014-a027, b001-b005, b014-b027 | Alignment matches printed text/lines to the template (SIFT + MAGSAC homography), not the page edge |
| Other papers / a stack of sheets in the frame | a001, b001 | Only features matching the printed cover are used; outliers are rejected |
| Perspective tilt and slight rotation | most photos | Homography removes it; printed grid lines are then re-found and cells snapped to them (max 15 px residual seen) |
| Shadows / uneven light hiding some printed lines | b025 (4 of 27 lines not found) | Missing lines are placed using the offset of the lines that were found; sheet flagged only if < 75% are found |
| Back-side print showing through the paper | faint mirrored text in many grid cells | Red-ink filter ignores grey/black ink |
| Thin, faint red strokes (low colour saturation after JPEG) | a012, a013, b012 roll digits | Red filter is relative to the paper around each stroke, not a fixed colour |
| Writing that overflows into the neighbouring cell | a001: tail of the Q3d "4" crosses into Q4d | Ink that only pokes in from a cell edge (< 30% deep) is ignored |
| Digits touching or crossing grid lines | b011, b014, b015 | Cells are cropped 6 px inside the snapped lines; black lines are ignored by the red filter |
| Leading zeros ("01", "05", "09") | a013, a014, b011, b015, b016 | Labels store the number (1, 5, 9); M3 digit splitting must expect 2 digits |
| Crossed-out / scribbled marks | b011 Q8a-b, b026 Q1a-d, b008 Q7c (scribble beside the 4) | Detected as ink (correct for M2); M4 adds a "not a number" class |
| A dash instead of a number | a011 Q4a | Same as crossed-out: ink, but not a number - treated as not attempted |
| Roll boxes left empty, roll written only in the header | b002 | Sheet flagged "review"; the header roll field is mapped (`header_roll_box`) for a later fallback |
| Header fields filled inconsistently (e.g. course name in "Day") | a001, b002 | Not used - only the roll boxes, marks grid and A/B letter are read |
| No blank PDF of the cover available | - | Template built from photo a012 (dark background, flat) with red ink painted out; see `scripts/build_template.py` |

Possible label issue spotted during M2: **b008 Q7b** looks like "13" on the photo but is labeled 3.
