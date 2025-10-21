# filename: pages5_Option_Chain_OI_Tracker_merged.py
import requests
import pandas as pd
import streamlit as st
from datetime import datetime
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="NIFTY & BANKNIFTY Option Chain - OI Tracker", layout="wide")

# ----------------- Auto-refresh (every 30 seconds) -----------------
_ = st_autorefresh(interval=30_000, limit=None, key="auto_refresh_30s")  # 30,000 ms = 30s

# ----------------- Helpers -----------------
@st.cache_data(ttl=15)  # small cache to reduce throttle; refreshes after 15s
def fetch_option_chain(symbol: str):
    """
    Returns parsed json from NSE option chain endpoint.
    We avoid try/except here and rely on response validation in the caller.
    """
    url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/"
    }
    s = requests.Session()
    # preflight to get cookies
    resp_base = s.get("https://www.nseindia.com", headers=headers, timeout=6)
    # if base call failed, return None so caller can show an error
    if resp_base.status_code != 200:
        return None
    r = s.get(url, headers=headers, timeout=12)
    if r.status_code != 200:
        return None
    return r.json()

def safe_int(x):
    try:
        return int(round(float(x)))
    except Exception:
        return 0

def safe_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default

def fmt_int(x):
    try:
        return f"{int(x):,d}"
    except Exception:
        return str(x)

def fmt_money(x):
    try:
        return f"{float(x):,.2f}"
    except Exception:
        return str(x)

# ----------------- UI -----------------
st.title("📊 NIFTY / BANKNIFTY Option Chain — OI Tracker")

symbol = st.radio("Select Index", ["NIFTY", "BANKNIFTY"], horizontal=True)

# Manual refresh button
if st.button("🔄 Refresh Now"):
    st.experimental_rerun()

# ----------------- Fetch data -----------------
raw = fetch_option_chain(symbol)
if not raw or not isinstance(raw, dict):
    st.error("Failed to fetch option chain from NSE or response invalid. Try manual refresh.")
    st.stop()

records = raw.get("records") or {}
expiry_dates = records.get("expiryDates") or []
data_list = records.get("data") or raw.get("filtered", {}).get("data") or raw.get("data") or []

# robust underlying lookup
underlying_value = records.get("underlyingValue") or raw.get("underlyingValue") or None
if underlying_value is None:
    for d in data_list:
        for side in ("CE", "PE"):
            s = d.get(side)
            if s and s.get("underlyingValue") is not None:
                underlying_value = s.get("underlyingValue")
                break
        if underlying_value is not None:
            break

if not expiry_dates and data_list:
    expiry_dates = sorted({d.get("expiryDate") for d in data_list if d.get("expiryDate")})

if not expiry_dates:
    st.error("Could not find expiry dates in the NSE response.")
    st.stop()

selected_expiry = st.selectbox(
    "Select Expiry (default = current week)",
    options=expiry_dates,
    index=0
)

# filter for selected expiry
filtered_rows = [r for r in data_list if r.get("expiryDate") == selected_expiry]
if not filtered_rows:
    st.error(f"No strikes found for selected expiry: {selected_expiry}")
    st.stop()

spot_price = safe_float(underlying_value, 0.0)

# ----------------- Build DataFrame (full expiry) -----------------
rows = []
for r in filtered_rows:
    strike = safe_int(r.get("strikePrice", 0))
    ce = r.get("CE") or {}
    pe = r.get("PE") or {}
    # intrinsic values (approx)
    ce_iv = max(spot_price - strike, 0)
    pe_iv = max(strike - spot_price, 0)
    ce_last = safe_float(ce.get("lastPrice", 0))
    pe_last = safe_float(pe.get("lastPrice", 0))
    # risk definitions kept similar but we prioritize clarity:
    # CE_Risk = CE intrinsic - PE last price (as one of your earlier variants)
    ce_risk = safe_int(ce_iv - pe_last)
    pe_risk = safe_int(pe_iv - ce_last)
    rows.append({
        "strikePrice": strike,
        "expiryDate": r.get("expiryDate"),
        # CE fields
        "CE_LTP": ce_last,
        "CE_OI": safe_int(ce.get("openInterest", 0)),
        "CE_Chg_OI": safe_int(ce.get("changeinOpenInterest", 0)),
        "CE_pchgOI": safe_int(ce.get("pchangeinOpenInterest", 0)),
        "CE_Risk": ce_risk,
        # PE fields
        "PE_LTP": pe_last,
        "PE_OI": safe_int(pe.get("openInterest", 0)),
        "PE_Chg_OI": safe_int(pe.get("changeinOpenInterest", 0)),
        "PE_pchgOI": safe_int(pe.get("pchangeinOpenInterest", 0)),
        "PE_Risk": pe_risk
    })

df_all = pd.DataFrame(rows).drop_duplicates(subset=["strikePrice"]).sort_values("strikePrice").reset_index(drop=True)
if df_all.empty:
    st.error("No strike data available after parsing.")
    st.stop()

# ----------------- ATM-centric selection for display (±6) -----------------
# find nearest strike index based on spot
atm_idx_full = (df_all["strikePrice"] - spot_price).abs().idxmin()
window_before = 6
window_after = 6
start_idx = max(0, int(atm_idx_full) - window_before)
end_idx = min(len(df_all) - 1, int(atm_idx_full) + window_after)
df_shown = df_all.iloc[start_idx:end_idx + 1].copy().reset_index(drop=True)
df_shown = df_shown.sort_values("strikePrice").reset_index(drop=True)
# ATM in shown window
atm_idx_shown = (df_shown["strikePrice"] - spot_price).abs().idxmin()
atm_strike = int(df_shown.loc[atm_idx_shown, "strikePrice"])

# ----------------- Derived metrics & PCR -----------------
# CE-PE diff risk
df_shown["CE_PE_Diff"] = df_shown["CE_Risk"] - df_shown["PE_Risk"]

# PCR on displayed strikes
total_pe_oi_shown = int(df_shown["PE_OI"].sum())
total_ce_oi_shown = int(df_shown["CE_OI"].sum())
total_pcr_shown = (total_pe_oi_shown / total_ce_oi_shown) if total_ce_oi_shown != 0 else float("inf")
trend_shown = "🟢 Bullish" if total_pcr_shown > 1 else "🔴 Bearish"

# ATM ±4 PCR (narrow)
start_atm4 = max(0, int(atm_idx_shown) - 4)
end_atm4 = min(len(df_shown) - 1, int(atm_idx_shown) + 4)
df_atm4 = df_shown.iloc[start_atm4:end_atm4 + 1]
atm4_pe_oi = int(df_atm4["PE_OI"].sum())
atm4_ce_oi = int(df_atm4["CE_OI"].sum())
atm4_pcr = (atm4_pe_oi / atm4_ce_oi) if atm4_ce_oi != 0 else float("inf")
atm4_trend = "🟢 Bullish" if atm4_pcr > 1 else "🔴 Bearish"

# ATM ±5 PCR (for reference)
start_atm5 = max(0, int(atm_idx_shown) - 5)
end_atm5 = min(len(df_shown) - 1, int(atm_idx_shown) + 5)
df_atm5 = df_shown.iloc[start_atm5:end_atm5 + 1]
atm5_pe_oi = int(df_atm5["PE_OI"].sum())
atm5_ce_oi = int(df_atm5["CE_OI"].sum())
atm5_pcr = (atm5_pe_oi / atm5_ce_oi) if atm5_ce_oi != 0 else float("inf")
atm5_trend = "🟢 Bullish" if atm5_pcr > 1 else "🔴 Bearish"

# ATM row percent changes
atm_row = df_shown.loc[atm_idx_shown]
atm_ce_pct = int(atm_row.get("CE_pchgOI", 0))
atm_pe_pct = int(atm_row.get("PE_pchgOI", 0))

# Rocket logic (kept similar to previous)
rocket_symbol = "🤔"
if (total_pcr_shown > 1) and (atm_row["CE_Risk"] > atm_row["PE_Risk"]):
    rocket_symbol = "🟢🚀"
elif (total_pcr_shown < 1) and (atm_row["PE_Risk"] > atm_row["CE_Risk"]):
    rocket_symbol = "🔴🚀"
else:
    if (total_pcr_shown > 1 and atm_row["CE_Risk"] > atm_row["PE_Risk"]) or (atm_pe_pct > 0 and atm_row["PE_OI"] > atm_row["CE_OI"]):
        rocket_symbol = "🟡⚠️"
    elif (total_pcr_shown < 1 and atm_row["PE_Risk"] < atm_row["CE_Risk"]) or (atm_ce_pct > 0 and atm_row["CE_OI"] > atm_row["PE_OI"]):
        rocket_symbol = "🟡⚠️"
    else:
        rocket_symbol = "🤔"

# ----------------- Prepare styled display DataFrame -----------------
display = df_shown.copy()
display["StrikeLabel"] = display["strikePrice"].apply(lambda s: f"[ATM] {int(s)}" if int(s) == atm_strike else f"{int(s)}")
display["SPOT"] = safe_int(spot_price)

display = display.rename(columns={"CE_pchgOI": "CE_%OI", "PE_pchgOI": "PE_%OI"})
display = display[[
    "CE_OI", "CE_%OI", "CE_Risk", "CE_PE_Diff", "CE_LTP",
    "StrikeLabel", "SPOT",
    "PE_LTP", "PE_Risk", "PE_%OI", "PE_OI"
]]

# ensure integer for display columns (LTP will be shown as int if requested)
for c in ["CE_OI","CE_%OI","CE_Risk","CE_PE_Diff","CE_LTP","SPOT","PE_LTP","PE_Risk","PE_%OI","PE_OI"]:
    display[c] = display[c].fillna(0)
    # Convert LTP columns to int for display consistency (as requested earlier)
    if c in ("CE_LTP","PE_LTP","SPOT"):
        display[c] = display[c].apply(lambda x: int(round(float(x))) if str(x) not in ("nan","None","") else 0)
    else:
        display[c] = display[c].astype(int)

max_ce_oi_shown = int(display["CE_OI"].max()) if not display["CE_OI"].empty else 0
max_pe_oi_shown = int(display["PE_OI"].max()) if not display["PE_OI"].empty else 0

ATM_BG = "#fff8cc"  # light yellow

def style_row(row):
    styles = [''] * len(row)
    col_idx = {col: i for i, col in enumerate(display.columns)}

    # CE%OI positive => shade CE side columns (light red)
    if int(row["CE_%OI"]) > 0:
        for c in ["CE_LTP", "CE_%OI", "CE_Risk", "CE_OI"]:
            styles[col_idx[c]] = 'background-color: #ffcdd2'

    # PE%OI positive => shade PE side columns (light green)
    if int(row["PE_%OI"]) > 0:
        for c in ["PE_LTP", "PE_%OI", "PE_Risk", "PE_OI"]:
            styles[col_idx[c]] = 'background-color: #c8e6c9'

    # Max OI emphasis within shown window (visual only)
    if int(row["CE_OI"]) == max_ce_oi_shown and max_ce_oi_shown > 0:
        styles[col_idx["CE_OI"]] = 'background-color: #e57373; font-weight: bold'
    if int(row["PE_OI"]) == max_pe_oi_shown and max_pe_oi_shown > 0:
        styles[col_idx["PE_OI"]] = 'background-color: #81c784; font-weight: bold'

    # CE-PE Diff color coding
    diff_val = int(row["CE_PE_Diff"])
    if diff_val > 0:
        styles[col_idx["CE_PE_Diff"]] = 'color: green; font-weight: 700'
    elif diff_val < 0:
        styles[col_idx["CE_PE_Diff"]] = 'color: red; font-weight: 700'
    else:
        styles[col_idx["CE_PE_Diff"]] = 'color: black'

    # Signed Risk colors
    for col in ["CE_Risk", "PE_Risk"]:
        val = int(row[col])
        if val > 0:
            styles[col_idx[col]] = (styles[col_idx[col]] + '; color: green').lstrip(';')
        elif val < 0:
            styles[col_idx[col]] = (styles[col_idx[col]] + '; color: red').lstrip(';')
        else:
            styles[col_idx[col]] = (styles[col_idx[col]] + '; color: black').lstrip(';')

    # ATM full-row highlight
    try:
        if isinstance(row["StrikeLabel"], str) and row["StrikeLabel"].startswith("[ATM]"):
            for i in range(len(styles)):
                styles[i] = (styles[i] + f'; background-color: {ATM_BG}') if styles[i] else f'background-color: {ATM_BG}'
            styles[col_idx["StrikeLabel"]] += '; border: 2px solid #000; font-weight: 700'
    except Exception:
        pass

    return styles

styled = display.style.apply(style_row, axis=1)

# ----------------- Top PCR display -----------------
st.markdown(f"### 🧭 Spot: **{safe_int(spot_price)}** ({symbol})")
st.markdown(
    f"**PCR (Put/Call Ratio on displayed strikes): {total_pcr_shown:.2f if total_pcr_shown!=float('inf') else '∞'} → {trend_shown} | "
    f"PCR (ATM ±4): {atm4_pcr:.2f if atm4_pcr!=float('inf') else '∞'} → {atm4_trend} | "
    f"PCR (ATM ±5): {atm5_pcr:.2f if atm5_pcr!=float('inf') else '∞'} → {atm5_trend} | {rocket_symbol}**"
)

# ----------------- Display table header & table -----------------
st.write("### 🔍 ATM ±6 Strike Option Chain (ascending strikes)")
# show the styled dataframe
try:
    st.dataframe(styled, use_container_width=True, hide_index=True)
except Exception:
    st.write(styled.to_html(), unsafe_allow_html=True)

# ----------------- Summary columns: Fresh OI summary (Directional) -----------------
col1, col2 = st.columns([1,1])
with col1:
    st.write("**🟢🔴 Fresh OI summary (Directional)**")
    ce_builds = int((df_shown["CE_pchgOI"] > 0).sum())
    pe_builds = int((df_shown["PE_pchgOI"] > 0).sum())
    both_unwind = int(((df_shown["CE_pchgOI"] < 0) & (df_shown["PE_pchgOI"] < 0)).sum())
    st.write(f"CE builds (resistance): {ce_builds}")
    st.write(f"PE builds (support): {pe_builds}")
    st.write(f"Both sides unwinding: {both_unwind}")

# ----------------- Max OI block (FULL expiry) - PLACED AFTER Fresh OI Summary -----------------
# compute max CE and max PE across full expiry (df_all)
max_ce_row = df_all.loc[df_all["CE_OI"].idxmax()] if (df_all["CE_OI"].notnull().any()) else None
max_pe_row = df_all.loc[df_all["PE_OI"].idxmax()] if (df_all["PE_OI"].notnull().any()) else None

with col2:
    st.write("**📌 Max OI (entire selected expiry)**")
    if max_ce_row is not None and int(max_ce_row["CE_OI"]) > 0:
        ce_strike = int(max_ce_row["strikePrice"])
        ce_oi = int(max_ce_row["CE_OI"])
        ce_ltp = int(round(float(max_ce_row.get("CE_LTP", 0))))
        ce_chg_oi = int(max_ce_row.get("CE_Chg_OI", 0))
        ce_pchg = int(max_ce_row.get("CE_pchgOI", 0))
        st.write(f"Max CE OI: {ce_strike} | OI: {fmt_int(ce_oi)} | LTP: {fmt_int(ce_ltp)} | Chg OI: {fmt_int(ce_chg_oi)} | OI %Δ: {ce_pchg:+d}%")
    else:
        st.write("Max CE OI: N/A")

    if max_pe_row is not None and int(max_pe_row["PE_OI"]) > 0:
        pe_strike = int(max_pe_row["strikePrice"])
        pe_oi = int(max_pe_row["PE_OI"])
        pe_ltp = int(round(float(max_pe_row.get("PE_LTP", 0))))
        pe_chg_oi = int(max_pe_row.get("PE_Chg_OI", 0))
        pe_pchg = int(max_pe_row.get("PE_pchgOI", 0))
        st.write(f"Max PE OI: {pe_strike} | OI: {fmt_int(pe_oi)} | LTP: {fmt_int(pe_ltp)} | Chg OI: {fmt_int(pe_chg_oi)} | OI %Δ: {pe_pchg:+d}%")
    else:
        st.write("Max PE OI: N/A")

# ----------------- Bottom ticker -----------------
st.markdown("---")
st.markdown(
    f"**Live Snapshot:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
    f"Spot: {safe_int(spot_price)} | "
    f"PCR (all shown): {total_pcr_shown:.2f if total_pcr_shown!=float('inf') else '∞'} → {trend_shown} | "
    f"PCR (ATM ±4): {atm4_pcr:.2f if atm4_pcr!=float('inf') else '∞'} → {atm4_trend} | "
    f"PCR (ATM ±5): {atm5_pcr:.2f if atm5_pcr!=float('inf') else '∞'} → {atm5_trend} | "
    f"{rocket_symbol}"
)
