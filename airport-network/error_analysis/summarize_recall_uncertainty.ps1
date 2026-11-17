param(
    [string]$OutputDirectory = $PSScriptRoot
)
$ErrorActionPreference = 'Stop'
$z = 1.959963984540054
$inputs = @(
    @{ Horizon = 'T-2h'; Dimension = 'carrier'; File = 't_minus_2h_by_carrier_min_n_1000.csv' },
    @{ Horizon = 'T-30m'; Dimension = 'carrier'; File = 't_minus_30m_by_carrier_min_n_1000.csv' },
    @{ Horizon = 'T-2h'; Dimension = 'origin'; File = 't_minus_2h_by_origin_min_n_1000.csv' },
    @{ Horizon = 'T-30m'; Dimension = 'origin'; File = 't_minus_30m_by_origin_min_n_1000.csv' }
)
$rows = [System.Collections.Generic.List[object]]::new()
foreach ($inputFile in $inputs) {
    foreach ($item in (Import-Csv -LiteralPath (Join-Path $OutputDirectory $inputFile.File))) {
        $tp = [int]$item.true_positives
        $fn = [int]$item.false_negatives
        $nDelay = $tp + $fn
        if ($nDelay -ne [int]$item.actual_delays -or $nDelay -le 0) {
            throw "Invalid confusion counts in $($inputFile.File) for $($item.group)"
        }
        $p = $tp / [double]$nDelay
        $z2 = $z * $z
        $denom = 1 + $z2 / $nDelay
        $center = ($p + $z2 / (2 * $nDelay)) / $denom
        $half = $z * [Math]::Sqrt(($p * (1 - $p) / $nDelay) + ($z2 / (4 * $nDelay * $nDelay))) / $denom
        $rows.Add([pscustomobject]@{
            horizon = $inputFile.Horizon
            dimension = $inputFile.Dimension
            group = $item.group
            scored_rows = [int]$item.n
            actual_delays = $nDelay
            true_positives = $tp
            false_negatives = $fn
            recall = $p
            recall_ci95_low = [Math]::Max(0.0, [double]($center - $half))
            recall_ci95_high = [Math]::Min(1.0, [double]($center + $half))
            interval_method = 'Wilson score, two-sided 95%'
        })
    }
}
$out = Join-Path $OutputDirectory 'recall_uncertainty_by_group.csv'
$rows | Export-Csv -LiteralPath $out -NoTypeInformation -Encoding utf8
Write-Output "Wrote $($rows.Count) subgroup intervals to $out"
