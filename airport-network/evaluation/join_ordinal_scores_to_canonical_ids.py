"""Re-key ordinal-based Airport/Network scores after a verified safe handoff.

This script never infers canonical IDs. A cohort member with no unique
ordinal mapping is retained with an explicit unavailable reason and null score.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd


HORIZONS = {
    "T−2h": ("t_minus_2h", 6_982_525),
    "T−30m": ("t_minus_30m", 6_982_091),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_csv(path: Path, *, id_column: str | None = None) -> pd.DataFrame:
    dtype = {id_column: "string"} if id_column else None
    return pd.read_csv(path, dtype=dtype)


def validate_mapping(
    path: Path,
    *,
    ordinal_column: str,
    id_column: str,
    expected_source_rows: int,
    expected_sha256: str | None,
) -> tuple[pd.DataFrame, set[str]]:
    digest = sha256(path)
    if expected_sha256 and digest.lower() != expected_sha256.lower():
        raise ValueError(f"Mapping SHA-256 mismatch: got {digest}, expected {expected_sha256}.")
    mapping = read_csv(path, id_column=id_column)
    missing = sorted({ordinal_column, id_column} - set(mapping.columns))
    if missing:
        raise ValueError(f"Mapping is missing columns: {missing}")
    if mapping[ordinal_column].isna().any() or mapping[ordinal_column].duplicated().any():
        raise ValueError("Mapping must contain exactly one row per nonmissing source ordinal.")
    numeric = pd.to_numeric(mapping[ordinal_column], errors="coerce")
    if numeric.isna().any() or (numeric < 1).any() or not numeric.eq(numeric.round()).all():
        raise ValueError("Mapping source ordinals must be positive 1-based integers.")
    if len(mapping) != expected_source_rows or int(numeric.min()) != 1 or int(numeric.max()) != expected_source_rows:
        raise ValueError(
            f"Mapping must cover source ordinals 1..{expected_source_rows}; "
            f"rows={len(mapping)}, min={numeric.min()}, max={numeric.max()}."
        )
    mapping[ordinal_column] = numeric.astype("int64")
    if mapping[ordinal_column].duplicated().any():
        raise ValueError("Mapping contains duplicate source ordinals after numeric normalization.")
    nonnull = mapping[id_column].dropna()
    duplicate_ids = set(nonnull[nonnull.duplicated(keep=False)].astype(str))
    return mapping[[ordinal_column, id_column]], duplicate_ids


def rekey_one_horizon(
    horizon: str,
    membership_path: Path,
    score_path: Path,
    mapping: pd.DataFrame,
    duplicate_ids: set[str],
    *,
    id_column: str,
    ordinal_column: str,
    expected_members: int | None,
    output_dir: Path,
) -> dict[str, Any]:
    members = read_csv(membership_path, id_column=id_column)
    if id_column not in members.columns or members[id_column].isna().any() or members[id_column].astype("string").str.strip().eq("").any():
        raise ValueError(f"{horizon} membership must contain nonmissing {id_column} values.")
    if members[id_column].duplicated().any():
        raise ValueError(f"{horizon} membership contains duplicate {id_column} values.")
    if expected_members is not None and len(members) != expected_members:
        raise ValueError(f"{horizon} membership rows={len(members)}; expected {expected_members}.")
    if "delay_15" in members.columns:
        member_target = pd.to_numeric(members["delay_15"], errors="coerce")
        if member_target.isna().any() or not member_target.isin([0, 1]).all():
            raise ValueError(f"{horizon} membership delay_15 must be complete and binary when provided.")
        members["delay_15"] = member_target.astype("int8")

    scores = read_csv(score_path)
    required_score_columns = {
        "cleaned_input_row_ordinal", "forecast_horizon", "delay_15", "delay_probability",
        "decision_threshold", "predicted_delay_15", "score_status", "unavailable_reason",
    }
    missing = sorted(required_score_columns - set(scores.columns))
    if missing:
        raise ValueError(f"{horizon} ordinal score export is missing columns: {missing}")
    score_target = pd.to_numeric(scores["delay_15"], errors="coerce")
    if score_target.isna().any() or not score_target.isin([0, 1]).all():
        raise ValueError(f"{horizon} ordinal score export delay_15 must be complete and binary.")
    scores["delay_15"] = score_target.astype("int8")
    if scores["cleaned_input_row_ordinal"].isna().any() or scores["cleaned_input_row_ordinal"].duplicated().any():
        raise ValueError(f"{horizon} score export must have unique nonmissing ordinals.")
    scores["cleaned_input_row_ordinal"] = pd.to_numeric(scores["cleaned_input_row_ordinal"], errors="coerce")
    if (
        scores["cleaned_input_row_ordinal"].isna().any()
        or (scores["cleaned_input_row_ordinal"] < 1).any()
        or not scores["cleaned_input_row_ordinal"].eq(scores["cleaned_input_row_ordinal"].round()).all()
    ):
        raise ValueError(f"{horizon} score export has invalid ordinals.")
    scores["cleaned_input_row_ordinal"] = scores["cleaned_input_row_ordinal"].astype("int64")
    max_source_ordinal = int(mapping[ordinal_column].max())
    extra_ordinals = scores["cleaned_input_row_ordinal"].gt(max_source_ordinal)
    if extra_ordinals.any():
        examples = scores.loc[extra_ordinals, "cleaned_input_row_ordinal"].head(5).tolist()
        raise ValueError(
            f"{horizon} score export contains ordinals outside the verified source mapping "
            f"(maximum {max_source_ordinal}); examples: {examples}."
        )
    if not scores["forecast_horizon"].astype(str).eq(horizon).all():
        raise ValueError(f"{horizon} score export contains another prediction horizon.")
    if not scores["score_status"].isin(["scored", "unavailable"]).all():
        raise ValueError(f"{horizon} score export has an invalid score_status.")
    scored_rows = scores["score_status"].eq("scored")
    probabilities = pd.to_numeric(scores["delay_probability"], errors="coerce")
    thresholds = pd.to_numeric(scores["decision_threshold"], errors="coerce")
    labels = pd.to_numeric(scores["predicted_delay_15"], errors="coerce")
    if probabilities[scored_rows].isna().any() or not probabilities[scored_rows].between(0, 1).all():
        raise ValueError(f"{horizon} scored rows need probabilities in [0, 1].")
    if thresholds[scored_rows].isna().any() or not thresholds[scored_rows].between(0, 1).all():
        raise ValueError(f"{horizon} scored rows need thresholds in [0, 1].")
    if thresholds[scored_rows].nunique(dropna=True) > 1:
        raise ValueError(f"{horizon} score export must use one recorded threshold for all scored rows.")
    if labels[scored_rows].isna().any() or not labels[scored_rows].isin([0, 1]).all():
        raise ValueError(f"{horizon} scored rows need binary predicted labels.")
    if not labels[scored_rows].astype(int).eq((probabilities[scored_rows] >= thresholds[scored_rows]).astype(int)).all():
        raise ValueError(f"{horizon} predicted labels conflict with recorded thresholds.")
    if probabilities[~scored_rows].notna().any() or thresholds[~scored_rows].notna().any() or labels[~scored_rows].notna().any():
        raise ValueError(f"{horizon} unavailable rows must have null score, threshold, and label.")
    reasons = scores["unavailable_reason"].fillna("").astype(str).str.strip()
    if ((~scored_rows) & reasons.eq("")).any() or (scored_rows & ~reasons.eq("")).any():
        raise ValueError(f"{horizon} unavailable rows require a reason; scored rows require a blank reason.")

    # Any repeated canonical ID is intentionally excluded from the lookup.
    # It is surfaced below as unavailable instead of choosing one ordinal.
    unique_map = mapping.loc[
        mapping[id_column].notna() & ~mapping[id_column].astype("string").isin(duplicate_ids),
        [ordinal_column, id_column],
    ]
    joined = members.merge(unique_map, on=id_column, how="left", validate="one_to_one", sort=False)
    if not joined[id_column].reset_index(drop=True).equals(members[id_column].reset_index(drop=True)):
        raise AssertionError("Canonical membership order changed during mapping join.")
    joined = joined.rename(columns={ordinal_column: "cleaned_input_row_ordinal"})
    joined = joined.merge(
        scores,
        on="cleaned_input_row_ordinal",
        how="left",
        validate="one_to_one",
        sort=False,
        suffixes=("", "_score"),
    )
    if not joined[id_column].reset_index(drop=True).equals(members[id_column].reset_index(drop=True)):
        raise AssertionError("Canonical membership order changed during score join.")

    if "delay_15_score" in joined.columns and "delay_15" in joined.columns:
        target_score = pd.to_numeric(joined["delay_15_score"], errors="coerce")
        target_member = pd.to_numeric(joined["delay_15"], errors="coerce")
        conflict = target_score.notna() & target_member.notna() & ~target_score.eq(target_member)
        if conflict.any():
            raise ValueError(f"{horizon} score targets conflict with the canonical membership for {int(conflict.sum())} IDs.")
        joined["delay_15"] = target_member.fillna(target_score)
        joined = joined.drop(columns="delay_15_score")

    # Keep the cohort row even when identity or score lookup is unavailable.
    mapping_missing = joined["cleaned_input_row_ordinal"].isna()
    mapping_ambiguous = joined[id_column].astype("string").isin(duplicate_ids)
    score_missing = ~mapping_missing & joined["score_status"].isna()
    final_status = joined["score_status"].fillna("unavailable").astype(str)
    reasons = joined["unavailable_reason"].fillna("").astype(str)
    final_status.loc[mapping_missing | mapping_ambiguous | score_missing] = "unavailable"
    reasons.loc[mapping_missing] = "canonical_mapping_missing"
    reasons.loc[mapping_ambiguous] = "canonical_mapping_not_one_to_one"
    reasons.loc[score_missing] = "ordinal_score_row_missing"
    joined["score_status"] = final_status
    joined["unavailable_reason"] = reasons
    unavailable = joined["score_status"].eq("unavailable")
    joined.loc[unavailable, ["delay_probability", "decision_threshold", "predicted_delay_15"]] = pd.NA
    if "feature_availability_status" not in joined.columns:
        joined["feature_availability_status"] = "retrospective_bts_availability_unverified"

    reason_counts = joined.loc[unavailable, "unavailable_reason"].value_counts().sort_index().to_dict()
    output_dir.mkdir(parents=True, exist_ok=True)
    slug, _ = HORIZONS[horizon]
    output_path = output_dir / f"airport_network_{slug}_canonical_scores.csv.gz"
    joined.to_csv(output_path, index=False, compression="gzip")
    return {
        "horizon": horizon,
        "membership_input": {"path": str(membership_path), "bytes": membership_path.stat().st_size, "sha256": sha256(membership_path)},
        "ordinal_score_input": {"path": str(score_path), "bytes": score_path.stat().st_size, "sha256": sha256(score_path)},
        "membership_rows": len(members),
        "mapped_unique_ids": int((~mapping_missing & ~mapping_ambiguous).sum()),
        "ambiguous_mapping_ids": int(mapping_ambiguous.sum()),
        "scored_rows": int(joined["score_status"].eq("scored").sum()),
        "unavailable_rows": int(unavailable.sum()),
        "score_coverage": float(joined["score_status"].eq("scored").mean()) if len(joined) else 0.0,
        "unavailable_reason_counts": {str(k): int(v) for k, v in reason_counts.items()},
        "output": str(output_path),
        "output_bytes": output_path.stat().st_size,
        "output_sha256": sha256(output_path),
        "validation": "PASS: complete membership rows retained, unique IDs/ordinals checked, no ambiguous mapping selected, target conflicts checked, horizon checked, and unavailable rows explicit",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapping", required=True, type=Path)
    parser.add_argument("--mapping-ordinal-column", default="cleaned_input_row_ordinal")
    parser.add_argument("--mapping-id-column", default="flight_id")
    parser.add_argument("--expected-mapping-sha256")
    parser.add_argument("--expected-source-rows", type=int, default=6_982_766)
    parser.add_argument("--t2h-membership", required=True, type=Path)
    parser.add_argument("--t30m-membership", required=True, type=Path)
    parser.add_argument("--t2h-scores", required=True, type=Path)
    parser.add_argument("--t30m-scores", required=True, type=Path)
    parser.add_argument("--source-run-manifest", required=True, type=Path,
                        help="Run manifest for the ordinal score exports; copied by hash into the join manifest.")
    parser.add_argument("--expected-t2h-members", type=int, default=HORIZONS["T−2h"][1])
    parser.add_argument("--expected-t30m-members", type=int, default=HORIZONS["T−30m"][1])
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    mapping, duplicate_ids = validate_mapping(
        args.mapping,
        ordinal_column=args.mapping_ordinal_column,
        id_column=args.mapping_id_column,
        expected_source_rows=args.expected_source_rows,
        expected_sha256=args.expected_mapping_sha256,
    )
    results = {
        "status": "PRELIMINARY_UNTIL_VALIDATED_MAPPING_AND_COHORT_MEMBERSHIP",
        "mapping": {
            "path": str(args.mapping),
            "bytes": args.mapping.stat().st_size,
            "sha256": sha256(args.mapping),
            "rows": len(mapping),
            "mapped_unique_ids": int(mapping[args.mapping_id_column].dropna().nunique()),
            "duplicate_canonical_ids": len(duplicate_ids),
            "expected_source_rows": args.expected_source_rows,
        },
        "source_score_run": {
            "path": str(args.source_run_manifest),
            "sha256": sha256(args.source_run_manifest),
            "metadata": json.loads(args.source_run_manifest.read_text(encoding="utf-8")),
        },
        "results": {},
    }
    for horizon, membership_path, score_path, expected_count in (
        ("T−2h", args.t2h_membership, args.t2h_scores, args.expected_t2h_members),
        ("T−30m", args.t30m_membership, args.t30m_scores, args.expected_t30m_members),
    ):
        results["results"][horizon] = rekey_one_horizon(
            horizon,
            membership_path,
            score_path,
            mapping,
            duplicate_ids,
            id_column=args.mapping_id_column,
            ordinal_column=args.mapping_ordinal_column,
            expected_members=expected_count,
            output_dir=args.output_dir,
        )
    manifest_path = args.output_dir / "airport_network_canonical_score_join_manifest.json"
    manifest_path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
