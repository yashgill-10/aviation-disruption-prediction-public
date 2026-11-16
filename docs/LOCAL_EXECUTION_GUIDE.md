# Local execution and reproduction

Use Python with `requirements.txt` installed. Commands below do not upload data or deploy Pages.

```powershell
python -m pip install -r requirements.txt
python -B src/data_readiness.py
python -B -m unittest discover -s tests -p "test_*score*.py" -v
python -B -m unittest discover -s tests -p test_shared_cohort_reconciliation.py -v
python -m http.server 8000 -d web
```

For a deliberate full hash pass, use `python -B src/data_readiness.py --verify-hashes`; it can take time because files are large. It does not recreate reconciliation scans.

Flight/Weather regeneration is local only and requires original models plus prepared inputs. Use the command recorded in `flight_weather_score_export_readiness_2024.md`; choose a new output directory because the exporter refuses overwrite. The corrected exporter will include the reporting flight number. Then run `src/validate_flight_weather_score_exports_2024.py`.

Omruthik's required local command, once his scorer is present, is:

```powershell
python -B <airport_network_score_exporter.py> --mapping <local cleaned_row_to_canonical_mapping.csv.gz> --horizon t_minus_2h --output <local t_minus_2h_scores.csv.gz>
```

Repeat for `t_minus_30m`; preserve the run manifest and feature provenance. Do not transfer flagged data through Git or personal cloud services.
