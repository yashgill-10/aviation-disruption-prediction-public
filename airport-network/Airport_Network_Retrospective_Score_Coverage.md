# Airport/Network retrospective score coverage and provenance

This report distinguishes the original full-year, source-ordinal exports from the later canonical-ID exports used in the joint comparison. The score files, model bundle, mapping, and row-level IDs are private and are not distributed here.

## Preliminary full-year exports — source ordinal

The original full-year exports used `cleaned_input_row_ordinal`, a 1-based source record ordinal. They retained unavailable rows rather than assigning a zero probability.

| Horizon | Source rows | Scored | Unavailable | Coverage |
| --- | ---: | ---: | ---: | ---: |
| T−2h | 6,982,766 | 6,982,656 | 110 | 99.998425% |
| T−30m | 6,982,332 | 6,982,222 | 110 | 99.998425% |

The unavailable reason was `duplicate_source_row_removed_before_training_features`. These ordinal diagnostics are useful for auditing the original export, but their counts are not the canonical evaluation denominators.

## Canonical retrospective cohorts — separately per horizon

The Phase 2 mapping preserves all 6,982,766 source ordinals. It assigns unambiguous IDs to 6,982,525 records; 220 ambiguous records, one identity-incomplete record, and 20 unmatched records remain without canonical IDs. The unresolved records were not assigned IDs by inference.

The verified retrospective membership files contain 6,982,525 canonical IDs at T−2h and 6,982,091 at T−30m. The canonical Airport/Network score exports were validated against those exact horizon-specific memberships and report 100% coverage: 6,982,525/6,982,525 at T−2h and 6,982,091/6,982,091 at T−30m. These files and their row-level validation evidence remain in the private handoff; the public summary is in `../docs/PROJECT_COMPLETION_STATUS.md`.

The memberships use final BTS cancellation and actual-departure outcomes to define retrospective eligibility. They do not prove a flight could have been identified as eligible at prediction time. Never substitute matching aggregate counts for exact ID-set reconciliation.

## October–December component comparison

The matched canonical comparison has 1,768,340 common scored flights at T−2h and 1,768,222 at T−30m. This differs by 39 from each preliminary ordinal-keyed error-analysis population because 39 ambiguous records per horizon were retained in the ordinal diagnostics but have no canonical ID. They are excluded from the canonical matched comparison without inferred identities. See `../docs/PROJECT_COMPLETION_STATUS.md` for per-model results.

## Artifact provenance and limitations

The preliminary exports had recorded SHA-256 values documented in the private run manifest: T−2h `3058e8bb078714ed24278e23c6cbfeb614a0c56b91483844585acb28512bf918`; T−30m `cc73133c83821bcc4a4cac7a907002fdb4061d47152474148daaa32705293fef`. Row-level files are deliberately not published. The bundle SHA recorded for that score run is `9dca5d3bb2e8e2af0cf9bca3a2245584b83b10e2cfb0e883fb90388fd0b87b5a`.

The score-run manifest records builder SHA-256 `a77c70db0e033e85561186c7c947bd8957b024edfd7630e83e356ed175fe5b36`; a contemporaneous local-builder field records `c107a5eb379da9830b7cb288082b1807422f5cfa43af7f9ba6283d3a1698a3cf`. Neither is the byte hash of the currently published reference builder. The exact original scoring-time builder was not recovered, so this public implementation is a reference and full code-level score reproduction is unverified.

Schedule snapshot/revision times, BTS publication or ingestion times, recent airport event timestamps, and live cancellation/departure eligibility are unverified. Thus the results are retrospective research evidence, not operational availability or live-readiness evidence.
