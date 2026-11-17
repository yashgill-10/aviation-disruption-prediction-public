# M3 exploratory interpretation and error analysis

## Scope

This review compares saved Flight/Weather, Airport/Network, and M3 probabilities on identical canonical IDs and labels in November–December 2024: 1,159,155 flights at T−2h and 1,159,076 at T−30m. M3 was fit on September probabilities; its decision threshold was selected on October. November–December was already inspected, so this is exploratory rather than a blind final test. No base model was retrained and no base predictions were regenerated.

## Saved matched metrics

| Horizon | Model | AP | ROC-AUC | Brier | Log loss | Precision | Recall | F1 | Balanced accuracy | Decision threshold |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| T−2h | Flight/Weather | 0.327948 | 0.676451 | 0.139181 | 0.444977 | 0.273891 | 0.617136 | 0.379400 | 0.628216 | 0.217442 |
| T−2h | Airport/Network | 0.322279 | 0.668014 | 0.213314 | 0.617187 | 0.262302 | 0.615436 | 0.367832 | 0.616919 | 0.470405 |
| T−2h | M3 stack | 0.344739 | 0.684306 | 0.137256 | 0.439124 | 0.259718 | 0.689488 | 0.377310 | 0.628104 | 0.178095 |
| T−30m | Flight/Weather | 0.327614 | 0.677258 | 0.139040 | 0.444348 | 0.275969 | 0.608435 | 0.379711 | 0.628237 | 0.218222 |
| T−30m | Airport/Network | 0.342772 | 0.679061 | 0.208690 | 0.608083 | 0.274216 | 0.606095 | 0.377596 | 0.626196 | 0.463141 |
| T−30m | M3 stack | 0.357079 | 0.691513 | 0.136060 | 0.435820 | 0.264095 | 0.691795 | 0.382260 | 0.633381 | 0.175470 |

Metrics and thresholds above were reread from `m3_evaluation_report.json`; that report also records the confusion matrices and score-export validation.

## Interpretation

- **Ranking:** M3 has the highest average precision and ROC-AUC among these three models at both horizons on this sample.
- **Probability quality:** M3 has lower Brier score and log loss than both base models at both horizons. This is a retrospective comparison and does not validate calibration on a future untouched period.
- **Operating tradeoff:** M3 recall is higher and precision lower than both component models at both horizons. It catches more delayed flights while generating more false alerts. The chosen thresholds maximize F1 on October, not an operational cost function.
- **Thresholded metrics:** At T−2h, M3 F1 is below Flight/Weather F1 (0.377310 vs 0.379400) and balanced accuracy is nearly tied but slightly lower (0.628104 vs 0.628216). At T−30m, M3 F1 and balanced accuracy exceed both base models in this sample.
- **No causal claim:** These are matched retrospective predictions. The comparisons do not show that Airport/Network features caused M3's changes; a Flight/Weather-only M3 ablation and a predeclared operating-cost analysis were not part of this experiment.

## Error and uncertainty limits

The threshold tradeoff is material: M3's higher recall accompanies lower precision at both cutoffs. A team deciding whether the model is useful must specify the relative costs of missed delays and false alerts before selecting an operating threshold. The October F1 threshold is only an experimental choice.

No confidence intervals or clustered uncertainty estimates were calculated. Flights share dates, airports, and operational disruptions, so treating all rows as independent would overstate the effective sample size. Repeated periods and a future untouched period are needed before making a generalization claim.

The Airport/Network scoring-time feature-builder hash still differs from the available builder; retrospective BTS feature availability timestamps are unverified. Flight/Weather scoring/calibrator implementation was not independently available locally. These provenance gaps, plus prior inspection of November–December, mean the metrics must stay exploratory and do not establish live readiness.

## Decision

For the inspected November–December sample, the stack has better ranking and probability metrics and a recall-for-precision tradeoff. It does not uniformly outperform the components on thresholded metrics at T−2h, and none of these values is a blind final estimate. A future evaluation period and its split-lock protocol remain provisional until data coverage, prospective timestamped collection, and joint approval are established.
