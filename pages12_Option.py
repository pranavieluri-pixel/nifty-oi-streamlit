# filename: pages11_Option.py
import requests
import pandas as pd
import streamlit as st
from datetime import datetime
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="NIFTY & BANKNIFTY Option Chain - OI Tracker", layout="wide")

# ----------------- Auto-refresh (30 seconds) -----------------
# triggers a rerun every 30000 ms (30s)
_ = st_autorefresh(interval=30000, limit=None, key="refresh_counter")

# ----------------- Helpers -----------------
def fetch_option_chain(symbol: str):
    url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/"
    }
    s = requests.Session()
    # initial visit to set cookies (NSE often needs this)
    s.get("https://www.nseindia.com", headers=headers, timeout=5)
    r = s.get(url, headers=headers, timeout=10)
    r.raise_for_status()
    return r.json()

def safe_int(x):
    try:
        return int(round(float(x)))
    except Exception:
        return 0

# ----------------- UI -----------------
st.title("📊 NIFTY / BANKNIFTY Option Chain — OI Tracker")
symbol = st.radio("Select Index", ["NIFTY", "BANKNIFTY"], horizontal=True)

# Manual refresh (label chosen: ♻️ Manual Refresh)
if st.button("♻️ Manual Refresh"):
    st.rerun()

# ----------------- Fetch data -----------------
try:
    raw = fetch_option_chain(symbol)
except Exception as e:
    st.error(f"Failed to fetch option chain: {e}")
    st.stop()

records = raw.get("records") or {}
expiry_dates = records.get("expiryDates") or []
data_list = records.get("data") or raw.get("filtered", {}).get("data") or raw.get("data") or []

# robust underlying value lookup
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

# build expiry list if missing
if not expiry_dates and data_list:
    expiry_dates = sorted({d.get("expiryDate") for d in data_list if d.get("expiryDate")})

if not expiry_dates:
    st.error("Could not find expiry dates in the NSE response.")
    st.stop()

# ----------------- Expiry selection -----------------
selected_expiry = st.selectbox(
    "Select Expiry (default = current week)",
    options=expiry_dates,
    index=0
)

filtered_rows = [r for r in data_list if r.get("expiryDate") == selected_expiry]
if not filtered_rows:
    st.error(f"No strikes found for selected expiry: {selected_expiry}")
    st.stop()

spot_price = float(underlying_value) if underlying_value is not None else 0.0

# ----------------- Build DataFrame -----------------
rows = []
for r in filtered_rows:
    strike = safe_int(r.get("strikePrice", 0))
    ce = r.get("CE") or {}
    pe = r.get("PE") or {}

    # intrinsic values (IV)
    ce_iv = max(spot_price - strike, 0)    # CE intrinsic
    pe_iv = max(strike - spot_price, 0)    # PE intrinsic

    ce_ltp = safe_int(ce.get("lastPrice", 0))
    pe_ltp = safe_int(pe.get("lastPrice", 0))

    # risk formulas as per your provided code:
    # CE_Risk = CE_LTP - CE_IV ; PE_Risk = PE_LTP - PE_IV
    ce_risk = safe_int(ce_ltp - ce_iv)
    pe_risk = safe_int(pe_ltp - pe_iv)

    rows.append({
        "strikePrice": strike,
        "CE_OI": safe_int(ce.get("openInterest", 0)),
        "CE_pchgOI": safe_int(ce.get("pchangeinOpenInterest", 0)),
        "CE_LTP": ce_ltp,
        "CE_Risk": ce_risk,
        "PE_LTP": pe_ltp,
        "PE_pchgOI": safe_int(pe.get("pchangeinOpenInterest", 0)),
        "PE_OI": safe_int(pe.get("openInterest", 0)),
        "PE_Risk": pe_risk
    })

df = pd.DataFrame(rows).drop_duplicates(subset=["strikePrice"]).sort_values("strikePrice").reset_index(drop=True)

if df.empty:
    st.error("No strike data available.")
    st.stop()

# ----------------- ATM-centric selection (±5 strikes) -----------------
atm_idx_full = (df["strikePrice"] - spot_price).abs().idxmin()
window_before = 5
window_after = 5
start_idx = max(0, int(atm_idx_full) - window_before)
end_idx = min(len(df) - 1, int(atm_idx_full) + window_after)
df_filtered = df.iloc[start_idx:end_idx + 1].copy().reset_index(drop=True)

# ensure ascending order
df_filtered = df_filtered.sort_values("strikePrice").reset_index(drop=True)

# recompute ATM index & strike
atm_idx_filtered = (df_filtered["strikePrice"] - spot_price).abs().idxmin()
atm_strike = int(df_filtered.loc[atm_idx_filtered, "strikePrice"])

# ----------------- Derived columns -----------------
# CE-PE difference column
df_filtered["CE_PE_Diff"] = df_filtered["CE_Risk"] - df_filtered["PE_Risk"]

# PCR calculations
total_pe_oi = int(df_filtered["PE_OI"].sum())
total_ce_oi = int(df_filtered["CE_OI"].sum())
total_pcr = (total_pe_oi / total_ce_oi) if total_ce_oi != 0 else float("inf")
trend = "🟢 Bullish" if total_pcr > 1 else "🔴 Bearish"

# ATM ±4 PCR
start_atm_idx = max(0, int(atm_idx_filtered) - 4)
end_atm_idx = min(len(df_filtered) - 1, int(atm_idx_filtered) + 4)
df_atm_window = df_filtered.iloc[start_atm_idx:end_atm_idx+1]
atm_pe_oi = int(df_atm_window["PE_OI"].sum())
atm_ce_oi = int(df_atm_window["CE_OI"].sum())
atm_pcr = (atm_pe_oi / atm_ce_oi) if atm_ce_oi != 0 else float("inf")
atm_trend = "🟢 Bullish" if atm_pcr > 1 else "🔴 Bearish"

# ATM ±5 PCR (for reference)
start_atm_idx_5 = max(0, int(atm_idx_filtered) - 5)
end_atm_idx_5 = min(len(df_filtered) - 1, int(atm_idx_filtered) + 5)
df_atm_window_5 = df_filtered.iloc[start_atm_idx_5:end_atm_idx_5+1]
atm5_pe_oi = df_atm_window_5["PE_OI"].sum()
atm5_ce_oi = df_atm_window_5["CE_OI"].sum()
atm5_pcr = (atm5_pe_oi / atm5_ce_oi) if atm5_ce_oi != 0 else float("inf")
atm5_trend = "🟢 Bullish" if atm5_pcr > 1 else "🔴 Bearish"

# ATM row percent change values for rocket logic
atm_row = df_filtered.iloc[atm_idx_filtered]
atm_ce_pct = int(atm_row.get("CE_pchgOI", 0))
atm_pe_pct = int(atm_row.get("PE_pchgOI", 0))

# ----------------- Rocket logic (your chosen rules) -----------------
rocket_symbol = "⚪"
rocket_text = "Neutral"

if (total_pcr > 1) and (atm_pe_oi > atm_ce_oi) and (atm_pe_pct > 0):
    rocket_symbol = "🟢🚀"
    rocket_text = "Strong Bullish"
elif (total_pcr < 1) and (atm_ce_oi > atm_pe_oi) and (atm_ce_pct > 0):
    rocket_symbol = "🔴🚀"
    rocket_text = "Strong Bearish"
else:
    # partial confirmations or divergence
    if (total_pcr > 1 and atm_pe_oi > atm_ce_oi) or (atm_pe_pct > 0 and atm_pe_oi > atm_ce_oi):
        rocket_symbol = "🟡⚠️"
        rocket_text = "Bullish but Risky"
    elif (total_pcr < 1 and atm_ce_oi > atm_pe_oi) or (atm_ce_pct > 0 and atm_ce_oi > atm_pe_oi):
        rocket_symbol = "🟡⚠️"
        rocket_text = "Bearish but Risky"
    else:
        rocket_symbol = "🤔"
        rocket_text = "Conflict / Wait"

# ----------------- Prepare display table (symmetric layout) -----------------
display = df_filtered.copy()
display["StrikeLabel"] = display["strikePrice"].apply(lambda s: f"[ATM] {int(s)}" if int(s) == atm_strike else f"{int(s)}")
display["SPOT"] = safe_int(spot_price)

# reorder and rename as requested
display = display.rename(columns={"CE_pchgOI": "CE_%OI", "PE_pchgOI": "PE_%OI"})
display = display[[
    "CE_OI",
    "CE_%OI",
    "CE_Risk",
    "CE_PE_Diff",
    "CE_LTP",
    "StrikeLabel",
    "SPOT",
    "PE_LTP",
    "PE_Risk",
    "PE_%OI",
    "PE_OI"
]]

# Ensure integer types
for c in ["CE_OI","CE_%OI","CE_Risk","CE_PE_Diff","CE_LTP","SPOT","PE_LTP","PE_Risk","PE_%OI","PE_OI"]:
    display[c] = display[c].fillna(0).astype(int)

# ----------------- Styling (no try/except — using validation checks) -----------------
max_ce_oi = int(display["CE_OI"].max()) if not display["CE_OI"].empty else 0
max_pe_oi = int(display["PE_OI"].max()) if not display["PE_OI"].empty else 0

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

    # Max OI emphasis
    if int(row["CE_OI"]) == max_ce_oi and max_ce_oi > 0:
        styles[col_idx["CE_OI"]] = 'background-color: #e57373; font-weight: bold'
    if int(row["PE_OI"]) == max_pe_oi and max_pe_oi > 0:
        styles[col_idx["PE_OI"]] = 'background-color: #81c784; font-weight: bold'

    # CE-PE Diff color coding
    diff_val = int(row["CE_PE_Diff"])
    if diff_val > 0:
        styles[col_idx["CE_PE_Diff"]] = 'color: green; font-weight: 700'
    elif diff_val < 0:
        styles[col_idx["CE_PE_Diff"]] = 'color: red; font-weight: 700'
    else:
        styles[col_idx["CE_PE_Diff"]] = 'color: black'

    # Signed Risk colors for CE_Risk / PE_Risk
    for col in ["CE_Risk", "PE_Risk"]:
        val = int(row[col])
        prev = styles[col_idx[col]]
        if val > 0:
            styles[col_idx[col]] = (prev + '; color: green') if prev else 'color: green'
        elif val < 0:
            styles[col_idx[col]] = (prev + '; color: red') if prev else 'color: red'
        else:
            styles[col_idx[col]] = (prev + '; color: black') if prev else 'color: black'

    # Entire ATM row highlight (full background) using validation check
    strike_label = str(row["StrikeLabel"])
    if strike_label.startswith("[ATM]"):
        for i in range(len(styles)):
            styles[i] = (styles[i] + f'; background-color: {ATM_BG}') if styles[i] else f'background-color: {ATM_BG}'
        # bold + border for StrikeLabel cell
        styles[col_idx["StrikeLabel"]] = (styles[col_idx["StrikeLabel"]] + '; border: 2px solid #000; font-weight: 700') if styles[col_idx["StrikeLabel"]] else '; border: 2px solid #000; font-weight: 700'

    return styles

styled = display.style.apply(style_row, axis=1)

# ----------------- Top PCR display -----------------
st.markdown(f"### 🧭 Spot: **{safe_int(spot_price)}** ({symbol})")
pcr_display = (f"{total_pcr:.2f}" if total_pcr != float("inf") else "∞")
atm_pcr_display = (f"{atm_pcr:.2f}" if atm_pcr != float("inf") else "∞")
atm5_pcr_display = (f"{atm5_pcr:.2f}" if atm5_pcr != float("inf") else "∞")
st.markdown(f"**PCR (Put/Call Ratio on displayed strikes): {pcr_display} → {trend}**")
st.markdown(f"**PCR (ATM ±4 strikes): {atm_pcr_display} → {atm_trend}**")
st.markdown(f"**PCR (ATM ±5 strikes): {atm5_pcr_display} → {atm5_trend}**")

# ----------------- Display table -----------------
st.write("### 🔍 ATM ±5 Strike Option Chain (ascending strikes)")
try:
    st.dataframe(styled, use_container_width=True, hide_index=True)
except Exception:
    st.write(styled.to_html(), unsafe_allow_html=True)

# ----------------- Summary columns -----------------
col1, col2 = st.columns([1,1])
with col1:
    st.write("**📌 Max OI (on shown strikes)**")
    st.write(f"Max CE OI: {max_ce_oi}")
    st.write(f"Max PE OI: {max_pe_oi}")

with col2:
    st.write("**🟢🔴 Fresh OI summary (Directional)**")
    ce_builds = int((df_filtered["CE_%OI"] > 0).sum())
    pe_builds = int((df_filtered["PE_%OI"] > 0).sum())
    both_unwind = int(((df_filtered["CE_%OI"] < 0) & (df_filtered["PE_%OI"] < 0)).sum())
    st.write(f"CE builds (resistance): {ce_builds}")
    st.write(f"PE builds (support): {pe_builds}")
    st.write(f"Both sides unwinding: {both_unwind}")

# ----------------- Bottom ticker -----------------
st.markdown("---")
st.markdown(
    f"**Live Snapshot:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
    f"Spot: {safe_int(spot_price)} | "
    f"PCR (all shown): {pcr_display} → {trend} | "
    f"PCR (ATM ±4): {atm_pcr_display} → {atm_trend} | "
    f"PCR (ATM ±5): {atm5_pcr_display} → {atm5_trend} | "
    f"{rocket_symbol} {rocket_text}"
)
