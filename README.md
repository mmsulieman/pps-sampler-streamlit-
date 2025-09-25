
# Kebele-level PPS Village Sampler (Streamlit)

Upload a 4-column frame — **Woreda | Kebele | Village | HHs** — and this app performs **kebele-level PPS sampling** to select villages. You can choose **Systematic** (default) or **Independent** PPS and configure **m** per kebele.

## Run locally
```bash
python -m venv .venv
# Windows
.venv\Scriptsctivate
# Mac/Linux
source .venv/bin/activate

pip install -r requirements.txt
streamlit run app.py
```

## Deploy on Streamlit Cloud
1. Push this repo to GitHub.
2. Go to https://streamlit.io/cloud → **New app**.
3. Pick this repo and `app.py`, then **Deploy**.
4. Share the URL with your team.

## Input format
CSV/Excel with headers (case-insensitive): `Woreda, Kebele, Village, HHs`.

## Output
- **Sampled_Villages.csv** — the selected villages with the **same 4 columns**.
- **Sampled_Villages.xlsx** — adds a `Diagnostics` sheet.

## Notes
- Rows with non-positive HHs are dropped.
- If `m > #villages`, the app samples all available.
- Optional **Random seed** makes results reproducible (stable per kebele).
