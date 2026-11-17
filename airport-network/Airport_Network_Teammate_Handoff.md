# Airport/Network workstream handoff

The detailed public-source interface and limitations are in [Airport_Network_Integration_Handoff.md](Airport_Network_Integration_Handoff.md). The public repository contains no private model, dataset, mapping, or row-level score exports.

The saved estimator was checked for deserialization in an isolated environment with Python 3.13.2, NumPy 2.5.4, pandas 2.2.3, scikit-learn 1.6.1, and joblib 1.6.0. `joblib.load()` succeeded for both horizon pipelines. This is historical deserialization-only evidence; it is not a fresh test of this public checkout, inference, or evaluation. Original NumPy/joblib training versions are unknown.

The exact scoring-time Airport/Network feature-builder source was not recovered. The published builder is a reference implementation and is not byte-identical evidence of the original. The score export and matched comparison results are retrospective; see `../docs/PROJECT_COMPLETION_STATUS.md` and `Airport_Network_Retrospective_Score_Coverage.md`.

The T−2h and T−30m models/cohorts remain separate. Eligibility uses final BTS cancellation and reconstructed departure outcomes, so it is retrospective rather than verified live eligibility. Schedule-vintage, BTS ingestion/publication, and recent-state event timestamps remain unavailable. Airport/Network and M3 live inference have not been validated.
