import zipfile, sys, pathlib
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
z = zipfile.ZipFile(r'D:\Projects\aviation-disruption-prediction\handoff\airport-network\unverified_archive\airport_network_m3_final_submission_handoff.zip')
out = pathlib.Path('m3')
out.mkdir(exist_ok=True)

d = z.read('m3/train_evaluate_m3.py').decode('utf-8')
d = d.replace('import math\nimport platform', 'import math\nimport os\nimport platform', 1)

old = (
    'OUT = Path(__file__).resolve().parent\n'
    'BASE = OUT.parent\n'
    'ROOT = OUT.parents[2]\n'
    'FW_DIR = Path(r"C:\\Users\\Omruthik\\Downloads\\Aviation_Disruption_Handoff_20261103_224836\\data\\flight_weather_score_exports_2024_h2_v1")\n'
    'FW_MANIFEST_PATH = FW_DIR / "run_manifest.json"\n'
    'AN_MANIFEST_PATH = BASE / "run_manifest.json"\n'
    'PRELIM_MANIFEST_PATH = ROOT / "outputs" / "phase2_local_preliminary_scores" / "run_manifest.json"\n'
    'AN_BUILDER_PATH = ROOT / "outputs" / "build_airport_network_preliminary_scores.py"'
)
new = (
    'OUT = Path(__file__).resolve().parent\n'
    '# External inputs are private and not distributed. Point the environment\n'
    '# variables below at the approved local copies; the relative defaults preserve\n'
    '# the original workspace layout for the private reproduction environment.\n'
    'BASE = Path(os.environ.get("M3_AN_EXPORT_DIR", str(OUT.parent)))\n'
    '_workspace_root = OUT.parents[2] if len(OUT.parents) > 2 else OUT.parent\n'
    'ROOT = Path(os.environ.get("M3_WORKSPACE_ROOT", str(_workspace_root)))\n'
    'FW_DIR = Path(os.environ.get(\n'
    '    "M3_FLIGHT_WEATHER_EXPORT_DIR",\n'
    '    "data/flight_weather_score_exports_2024_h2_v1",\n'
    '))\n'
    'FW_MANIFEST_PATH = FW_DIR / "run_manifest.json"\n'
    'AN_MANIFEST_PATH = BASE / "run_manifest.json"\n'
    'PRELIM_MANIFEST_PATH = Path(os.environ.get(\n'
    '    "M3_PRELIM_MANIFEST",\n'
    '    str(ROOT / "outputs" / "phase2_local_preliminary_scores" / "run_manifest.json"),\n'
    '))\n'
    'AN_BUILDER_PATH = Path(os.environ.get(\n'
    '    "M3_AN_BUILDER",\n'
    '    str(ROOT / "outputs" / "build_airport_network_preliminary_scores.py"),\n'
    '))'
)
assert old in d, 'path block not found'
d = d.replace(old, new, 1)
assert 'Omruthik' not in d
(out / 'train_evaluate_m3.py').write_text(d, encoding='utf-8', newline='\n')

for m, dst in [
    ('m3/test_m3_contract.py', 'test_m3_contract.py'),
    ('m3/m3_evaluation_report.json', 'm3_evaluation_report.json'),
    ('m3/m3_run_manifest.json', 'm3_run_manifest.json'),
    ('m3/m3_interpretation_and_error_analysis.md', 'M3_interpretation_and_error_analysis.md'),
]:
    (out / dst).write_bytes(z.read(m))

readme = z.read('m3/README.md').decode('utf-8')
old_run = (
    "From the project workspace, run the existing scikit-learn 1.6.1 environment:\n\n"
    "```powershell\n"
    "& '.\\outputs\\phase2_ordinal_reconstruction\\venv_sklearn_1_6_1\\Scripts\\python.exe' -B '.\\outputs\\airport_network_canonical_integration\\m3_september_2024_exploratory_v1\\train_evaluate_m3.py'\n"
    "```"
)
new_run = (
    "The component score exports are private and are not distributed in this repository. "
    "To rerun the experiment in the private workspace, install scikit-learn 1.6.1 and run:\n\n"
    "```powershell\n"
    "python -B m3/train_evaluate_m3.py\n"
    "```\n\n"
    "Set `M3_FLIGHT_WEATHER_EXPORT_DIR` to the Flight/Weather score-export directory, "
    "`M3_AN_EXPORT_DIR` to the directory containing the Airport/Network `run_manifest.json`, "
    "score, target and feature exports, and optionally `M3_PRELIM_MANIFEST`, `M3_AN_BUILDER`, "
    "`M3_WORKSPACE_ROOT` for the provenance inputs. Without these private inputs the script "
    "cannot run; `test_m3_contract.py` exercises the input/partition helpers on synthetic data only."
)
assert old_run in readme, 'readme run block not found'
readme = readme.replace(old_run, new_run, 1)
readme += (
    "\n\n## Public-release note\n\n"
    "This is the actual retrospective M3 training/evaluation script recovered from the private "
    "final-submission handoff archive (`airport_network_m3_final_submission_handoff.zip`), with "
    "only the external input paths parameterized via environment variables. The trained "
    "`m3_*.joblib` models and row-level `m3_*_scores.csv.gz` exports remain private and are not "
    "distributed; their SHA-256 hashes are recorded in `m3_run_manifest.json` and "
    "`m3_evaluation_report.json`. The contract test is synthetic-only.\n"
)
(out / 'README.md').write_text(readme, encoding='utf-8', newline='\n')
print('written:', [p.name for p in out.iterdir()])
PY
