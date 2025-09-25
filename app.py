# app.py
# Streamlit app for kebele-level PPS village sampling
# Input: 4 columns -> Woreda | Kebele | Village | HHs
# Output: Sampled_Villages CSV + Excel (with Diagnostics)

import io
import math
import hashlib
from typing import Tuple
import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Kebele-level PPS Village Sampler",
    page_icon="🎯",
    layout="wide"
)

REQUIRED_COLS = ["woreda", "kebele", "village", "hhs"]


def norm_colnames(cols):
    return [str(c).strip().lower().replace("\n", " ").replace("\t", " ") for c in cols]


def read_input(file) -> pd.DataFrame:
    name = getattr(file, "name", "uploaded")
    ext = str(name).lower().split(".")[-1]

    if ext in ["xlsx", "xls"]:
        df = pd.read_excel(file, engine=None)
    elif ext in ["csv"]:
        try:
            df = pd.read_csv(file)
        except UnicodeDecodeError:
            df = pd.read_csv(file, encoding="cp1252")
    else:
        try:
            df = pd.read_csv(file)
        except Exception:
            raise ValueError("Unsupported file type. Use .xlsx, .xls, or .csv")

    df.columns = norm_colnames(df.columns)
    rename_map = {}
    for c in df.columns:
        if c in ["woreda"]: rename_map[c] = "woreda"
        if c in ["kebele"]: rename_map[c] = "kebele"
        if c in ["village", "village / ea", "ea", "enumeration area"]:
            rename_map[c] = "village"
        if c in ["hhs", "hh", "households", "# households (hh)", "households (hh)", "households (no)"]:
            rename_map[c] = "hhs"
    df = df.rename(columns=rename_map)

    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}. Found: {list(df.columns)}")

    df = df[REQUIRED_COLS].copy()
    for c in ["woreda", "kebele", "village"]:
        df[c] = df[c].astype(str).str.strip()
    df["hhs"] = pd.to_numeric(df["hhs"], errors="coerce").fillna(0).astype(float)
    df = df[df["hhs"] > 0].copy()
    df = df[(df["kebele"].str.len() > 0) & (df["village"].str.len() > 0)].copy()
    return df


def rng_for_group(seed_base, woreda, kebele):
    if seed_base is None or str(seed_base).strip() == "":
        return np.random.default_rng()
    s = f"{seed_base}::{woreda}::{kebele}"
    h = int(hashlib.blake2b(s.encode("utf-8"), digest_size=8).hexdigest(), 16)
    return np.random.default_rng(h)


def pps_select_independent(cum_high, rng, m, avoid_duplicates=True, max_redraws=1000):
    n = len(cum_high)
    if n == 0: return []

    def pick(u):
        idx = np.searchsorted(cum_high, u, side="left")
        if idx >= n:
            idx = n - 1
        return int(idx)

    if not avoid_duplicates:
        return [pick(u) for u in rng.random(m)]

    selected = set()
    attempts = 0
    while len(selected) < min(m, n) and attempts < max_redraws:
        selected.add(pick(rng.random()))
        attempts += 1

    if len(selected) < min(m, n):
        interval = 1.0 / max(min(m, n), 1)
        start = rng.random() * interval
        k = 0
        while len(selected) < min(m, n) and k < m * 3:
            u = start + (k % m) * interval
            u = u - math.floor(u)
            selected.add(pick(u))
            k += 1

    return sorted(selected)


def pps_select_systematic(cum_high, rng, m):
    n = len(cum_high)
    if n == 0: return []

    interval = 1.0 / max(m, 1)
    start = rng.random() * interval
    indices = []
    for k in range(m):
        u = start + k * interval
        if u >= 1.0:
            u -= math.floor(u)
        idx = int(np.searchsorted(cum_high, u, side="left"))
        if idx >= n:
            idx = n - 1
        indices.append(idx)
    seen, uniq = set(), []
    for i in indices:
        if i not in seen:
            seen.add(i)
            uniq.append(i)
    return uniq


def sample_kebele(df_k: pd.DataFrame, m: int, method: str, rng, avoid_duplicates=True) -> Tuple[pd.DataFrame, pd.DataFrame]:
    df_k = df_k.copy()
    total = df_k["hhs"].sum()
    if total <= 0:
        df_k["p"], df_k["cum_high"], df_k["cum_low"] = 0.0, 0.0, 0.0
        diag = df_k.copy()
        diag["method"], diag["m_requested"], diag["m_final"], diag["selected"] = method, m, 0, False
        return df_k.iloc[0:0], diag

    df_k["p"] = df_k["hhs"] / total
    df_k["cum_high"] = df_k["p"].cumsum()
    df_k["cum_low"] = df_k["cum_high"] - df_k["p"]

    nvill = len(df_k)
    m_eff = min(int(m), nvill) if nvill > 0 else 0
    if m_eff <= 0:
        diag = df_k.copy()
        diag["method"], diag["m_requested"], diag["m_final"], diag["selected"] = method, m, 0, False
        return df_k.iloc[0:0], diag

    if method == "Independent":
        idxs = pps_select_independent(df_k["cum_high"].to_numpy(), rng, m_eff, avoid_duplicates=avoid_duplicates)
    else:
        idxs = pps_select_systematic(df_k["cum_high"].to_numpy(), rng, m_eff)

    idxs = list(dict.fromkeys(idxs))
    sel = df_k.iloc[idxs].copy()[["woreda", "kebele", "village", "hhs"]]

    diag = df_k.copy()
    diag["method"] = method
    diag["m_requested"] = m
    diag["m_final"] = len(idxs)
    diag["selected"] = False
    diag.loc[diag.index.isin(df_k.index[idxs]), "selected"] = True

    return sel, diag


def run_pps(df: pd.DataFrame, method: str, m_default: int, threshold_n: int, m_large: int,
            use_fixed_m: bool, fixed_m: int, seed_base: str, avoid_dups_indep: bool):
    grp_cols = ["woreda", "kebele"]
    sampled_rows, diag_rows, kebele_summary = [], [], []

    for (w, k), g in df.groupby(grp_cols, dropna=False, sort=False):
        g = g.reset_index(drop=True)
        nvill = len(g)
        if use_fixed_m:
            m = max(int(fixed_m), 1)
        else:
            m = max(int(m_large if nvill >= int(threshold_n) else m_default), 1)

        rng = rng_for_group(seed_base, w, k)
        sel, diag = sample_kebele(
            g, m=m,
            method=("Independent" if method == "Independent" else "Systematic"),
            rng=rng,
            avoid_duplicates=bool(avoid_dups_indep)
        )
        sampled_rows.append(sel)
        diag_rows.append(diag)
        kebele_summary.append({
            "Woreda": w, "Kebele": k, "#Villages": nvill, "Method": method, "m_used": int(m),
            "Total HHs": int(g["hhs"].sum())
        })

    sampled = pd.concat(sampled_rows, ignore_index=True) if sampled_rows else df.iloc[0:0]
    diagnostics = pd.concat(diag_rows, ignore_index=True) if diag_rows else df.iloc[0:0]
    summary = pd.DataFrame(kebele_summary)
    return sampled, diagnostics, summary


def to_excel_bytes(sampled: pd.DataFrame, diagnostics: pd.DataFrame) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        sampled.to_excel(writer, index=False, sheet_name="Sampled_Villages")
        diagnostics.to_excel(writer, index=False, sheet_name="Diagnostics")
    return output.getvalue()


st.title("🎯 Kebele-level PPS Village Sampler")
st.caption("Upload a 4-column frame — **Woreda | Kebele | Village | HHs** — to run kebele-level PPS and download sampled villages.")

with st.sidebar:
    st.header("Settings")

    method = st.selectbox("PPS Method", ["Systematic", "Independent"], index=0,
                          help="Systematic avoids duplicates by design. Independent uses random draws; you can enable duplicate avoidance.")

    avoid_dups_indep = st.checkbox("(Independent) Avoid duplicates via re-draws", value=True,
                                   help="If Independent is selected, re-draw to avoid selecting the same village twice.")

    st.markdown("---")
    st.subheader("m (villages) per kebele")
    use_fixed_m = st.checkbox("Use fixed m for all kebeles", value=False)
    if use_fixed_m:
        fixed_m = st.number_input("Fixed m", min_value=1, max_value=30, value=2, step=1)
        m_default, threshold_n, m_large = 2, 7, 4
    else:
        m_default = st.number_input("Default m", min_value=1, max_value=30, value=2, step=1)
        threshold_n = st.number_input("If kebele has ≥ (villages)", min_value=2, max_value=1000, value=7, step=1)
        m_large = st.number_input("Use m =", min_value=1, max_value=30, value=4, step=1)
        fixed_m = m_default

    st.markdown("---")
    seed_base = st.text_input("Random seed (optional)", value="",
                              help="Provide any text/number to reproduce results; seed is applied per kebele.")

    st.markdown("---")
    st.caption("Tip: You can export both CSV and Excel with diagnostics.")

uploaded = st.file_uploader("Upload Excel/CSV (Woreda | Kebele | Village | HHs)", type=["xlsx", "xls", "csv"])

if uploaded is not None:
    try:
        df = read_input(uploaded)
        st.success(f"Loaded {len(df):,} rows across {df[['woreda','kebele']].drop_duplicates().shape[0]} kebele(s).")
        with st.expander("Preview (top 25 rows)"):
            st.dataframe(df.head(25), use_container_width=True)

        col1, col2 = st.columns(2)
        with col1:
            kebele_counts = df.groupby(["woreda", "kebele"], as_index=False)["village"].nunique()
            kebele_counts.rename(columns={"village": "#Villages"}, inplace=True)
            st.markdown("**Kebele → #Villages**")
            st.dataframe(kebele_counts, use_container_width=True, height=240)
        with col2:
            hh_sum = df.groupby(["woreda", "kebele"], as_index=False)["hhs"].sum()
            hh_sum.rename(columns={"hhs": "Total HHs"}, inplace=True)
            st.markdown("**Kebele → Total HHs**")
            st.dataframe(hh_sum, use_container_width=True, height=240)

        st.markdown("---")
        run_btn = st.button("🔁 Run PPS Sampling", type="primary")

        if run_btn:
            sampled, diagnostics, summary = run_pps(
                df=df,
                method=method,
                m_default=m_default,
                threshold_n=threshold_n,
                m_large=m_large,
                use_fixed_m=use_fixed_m,
                fixed_m=fixed_m,
                seed_base=seed_base,
                avoid_dups_indep=avoid_dups_indep
            )

            st.subheader("✅ Sampled Villages (all kebeles)")
            st.dataframe(sampled, use_container_width=True, height=320)

            st.subheader("📋 Kebele Summary (method & m used)")
            st.dataframe(summary, use_container_width=True, height=240)

            st.markdown("### ⬇️ Download Results")
            csv_bytes = sampled.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="Download Sampled_Villages.csv",
                data=csv_bytes,
                file_name="Sampled_Villages.csv",
                mime="text/csv"
            )
            xlsx_bytes = to_excel_bytes(sampled, diagnostics)
            st.download_button(
                label="Download Sampled_Villages.xlsx (with Diagnostics)",
                data=xlsx_bytes,
                file_name="Sampled_Villages.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

    except Exception as e:
        st.error(f"Error: {e}")
        st.stop()
else:
    st.info("Upload a file to begin. You can drag & drop a .csv or .xlsx file into this area.")


# ---------------------------------------------------------
# Footer (optional)
# ---------------------------------------------------------
st.markdown(
    """
    <hr style="margin-top:2rem; margin-bottom:0.5rem;">
    <div style="color:gray; font-size:0.9em;">
      © WFP Ethiopia – Somali Region (Jijiga AO) &nbsp;|&nbsp; PPS village sampling utility
    </div>
    """,
    unsafe_allow_html=True
)
