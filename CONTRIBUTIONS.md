# Final Contribution Statement

Yash Gill and Omruthik Nakkalapally completed this ES 335 project as a joint research effort. The work was organized into Flight/Weather and Airport/Network tracks, followed by shared cohort reconciliation, retrospective comparison, interpretation, and presentation. This statement records workstream ownership from the available source snapshots and handoff evidence; it does not assign percentages or claim per-file authorship where Git history is unavailable.

## Yash Gill — Flight/Weather and canonical identity

- Led the Flight/Weather workstream, including BTS flight preprocessing, historical flight features, weather-data integration and feature engineering, classifier experiments, calibration, threshold selection, and retrospective evaluation.
- Prepared saved Flight/Weather score exports and the local Flight/Weather inference prototype. That prototype is distinct from the static research website and is not a claim of live operational validation.
- Led the canonical BTS flight-identity and shared horizon-cohort pipeline, and supplied the corresponding mapping, cohort, manifest, and handoff evidence for matched evaluation.
- Coordinated the runtime artifact handoff for independent local research inspection. Restricted model and history artifacts are kept outside the public Git repository.

## Omruthik Nakkalapally — Airport/Network

- Led the Airport/Network workstream, including airport/network feature construction, Random Forest experiments, horizon-specific inference and score-export interfaces, feature-availability and retrospective eligibility audits, and aircraft-rotation pilot analysis.
- Adapted the Airport/Network outputs to the canonical flight IDs and shared retrospective cohorts, preserving explicit unavailable-score states and reasons in the export workflow.
- Prepared Airport/Network error analysis, validation and reconciliation evidence, model/results limitations, and the Airport/Network handoff documentation.
- Contributed responsive research-website corrections and documentation updates. The website displays saved results only.

## Joint research, evaluation, and presentation

- Formulated and refined the research questions and common evaluation protocol, including chronological splits, prediction horizons, target definition, leakage cautions, and the distinction between retrospective eligibility and live availability.
- Reviewed each other's work and reconciled Flight/Weather and Airport/Network outputs against shared canonical IDs and targets for the October–December 2024 component comparison.
- Developed and interpreted the M3 logistic-stacking results as a joint exploratory comparison. The M3 results use September meta-training, October threshold selection, and November–December retrospective evaluation; they are not a blind final test.
- Jointly prepared the research report, evidence-provenance discussion, limitations, website content, and final presentation materials.

## Inference and validity status

- A local Flight/Weather inference prototype exists for research inspection; it has not established production or live operational readiness.
- Airport/Network live inference has not been validated. The original scoring-time feature builder was not recovered, and timestamped availability of schedule-vintage and BTS-derived airport-state features at the cutoffs is unverified.
- M3 is a retrospective exploratory stack requiring both component scores; its live inference has not been validated.
- The shared eligibility cohort uses final BTS outcomes retrospectively. It does not prove that live cancellation/departure eligibility could have been established at prediction time.
- The evaluated 2024 periods were inspected during development, and 2025 was previously inspected. No untouched blind final evaluation or statistical-significance claim is made.
- A combined live prediction dashboard was not completed. The research website is a static presentation of saved retrospective evidence.

## Evidence and authorship limits

The workstream evidence is documented in `airport-network/`, `src/`, `tests/`, `reports/final_joint_exploratory_research_report.md`, and the final handoff and validation materials. The canonical identity and cohort artifacts were supplied with Yash's handoff; Airport/Network source, tests, exports, and reports are in the Airport/Network workstream. Available source snapshots do not retain complete Git authorship history for every historical file, so this record attributes work by documented workstream and handoff rather than guessing per-file authorship.

## AI-use disclosure

- **Omruthik — Airport/Network and website:** Used ChatGPT and Codex as assisting tools for code review and debugging, documentation drafting and revision, summarizing existing project evidence, and responsive website CSS work.
- **Yash — Flight/Weather:** Used ChatGPT, Codex/Codex CLI, and Devin as assisting tools for technical explanations and planning, codebase inspection and code review, debugging support, selected implementation, testing and reproducibility tasks, and drafting or refining documentation and summarizing saved run outputs.

These tools supported selected tasks under the contributors' direction; they did not independently establish scientific validity or verify research results. AI-assisted suggestions and changes were reviewed by the human contributors. The contributors remain responsible for the workstream decisions, reviewing code and sources, running and checking relevant work, interpreting analyses, and approving the submitted claims. Any course-required AI-use form or declaration should also be completed in the institution's required format.
