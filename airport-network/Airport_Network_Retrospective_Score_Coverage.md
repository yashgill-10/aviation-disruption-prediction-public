# Airport/Network retrospective score-export coverage

**Status: local retrospective source-ordinal coverage only.** This report records the available Airport/Network score coverage before canonical identity resolution. It does not report coverage on the approved canonical cohorts, live eligible flights, or operationally available features.

## Counts and score status

| Horizon | Retrospective ordinal cohort rows | Scored | Explicitly unavailable | Coverage | Unavailable reason |
| --- | ---: | ---: | ---: | ---: | --- |
| T−2h | 6,982,766 | 6,982,656 | 110 | 99.998425% | `duplicate_source_row_removed_before_training_features` (110) |
| T−30m | 6,982,332 | 6,982,222 | 110 | 99.998425% | `duplicate_source_row_removed_before_training_features` (110) |

Unavailable rows remain present with null probability and an explicit reason; they were not converted to zero. These preliminary exports use `cleaned_input_row_ordinal`, not canonical `flight_id`.

## Local artifact identity

All six local compressed artifacts below were re-hashed and matched the recorded SHA-256 values on 2026-11-03:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| Preliminary full-year T−2h scores | 312,859,043 | `3058e8bb078714ed24278e23c6cbfeb614a0c56b91483844585acb28512bf918` |
| Preliminary full-year T−30m scores | 320,453,907 | `cc73133c83821bcc4a4cac7a907002fdb4061d47152474148daaa32705293fef` |
| Retrospective eligible T−2h score export | 246,532,057 | `04bfee3e391e88632bb4d5dd3c8ed64ce5bdf3984a231886946693d570617756` |
| Retrospective eligible T−30m score export | 251,577,334 | `57f264e0a75957304ba666f4b08bff8074d5b9f8df154d67d2f44aa8dda56296` |
| Local T−2h ordinal cohort list | 14,751,951 | `6b935b880c5fa06a728c00b8253f0dd57b5d8848aaafe7bbc14b5f1bbca1c30a` |
| Local T−30m ordinal cohort list | 14,752,955 | `b52ad078f690377c26333ede121b4e1dbaf20b0058b59346fa43d4e84b4c46e6` |

The filtered export manifest also records the model bundle SHA-256 `9dca5d3bb2e8e2af0cf9bca3a2245584b83b10e2cfb0e883fb90388fd0b87b5a`, thresholds, source score hashes, and the exact count/status totals. The underlying row-level score files are not included in this repository or handoff ZIP.

## Canonical coverage is not yet established

The independent reconciliation reports unambiguous canonical cohort counts of 6,982,525 at T−2h and 6,982,091 at T−30m. The local retrospective ordinal populations contain 241 more rows per horizon. The reconciliation accounts for cleaned-to-raw and identity exceptions, but the verified mapping and cohort membership are not available in this workspace. Therefore the 110 unavailable score rows cannot yet be assigned to or excluded from the canonical cohort, and canonical score coverage cannot be computed from counts alone.

The row-ordinal cohorts were constructed from final BTS status fields (`CANCELLED`, `DEP_DELAY`, and `delay_15`). They are retrospective outcome-derived populations, not live eligibility. Recent airport activity and prior BTS aggregates lack timestamped evidence of when they were observable. The score-run manifest's recorded builder hash also differs from the available builder hash; the exact scoring builder remains unresolved.

## Next validation after approved transfer

After the mapping and cohort files are available through an IT-cleared channel, verify their hashes, join each horizon by the preserved source ordinal, and compare exact ID membership. Keep every canonical cohort row, preserve explicit unavailable statuses/reasons, and report scored/unavailable coverage on those exact IDs. Do not infer identities or treat matching counts as proof of matching membership.
