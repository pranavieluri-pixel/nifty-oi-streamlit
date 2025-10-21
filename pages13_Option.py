# filename: pages_Option_Chain_Full.py
import requests
import pandas as pd
import streamlit as st
from datetime import datetime
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="NIFTY & BANKNIFTY Option Chain - Full OI Tracker", layout="wide")

# ----------------- Auto-refresh (every 30 sec) -----------------
_ = st_autorefresh(interval=30*1000, limit=None, key="auto_refresh")  # 30 sec

# ----------------- Helpers -----------------
def fetch_option_chain(symbol):
    url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/"
    }
    s = requests.Session()
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
st.title("📊 NIFTY / BANKNIFTY Option Chain — Full OI Tracker")
symbol = st.radio("Select Index", ["NIFTY", "BANKNIFTY"], horizontal=True)

# Manual refresh button
if st.button("🔄 Refresh Now"):
    st.experimental_rerun()

# ----------------- Fetch data -----------------
try:
    raw = fetch_option_chain(symbol)
except Exception as e:
    st.error(f"Failed to fetch option chain: {e}")
    st.stop()

records = raw.get("records") or {}
expiry_dates = records.get("expiryDates") or []
data_list = records.get("data") or raw.get("filtered", {}).get("data") or raw.get("data") or []

underlying_value = records.get("underlyingValue") or raw.get("underlyingValue")
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
    
    ce_ltp = safe_int(ce.get("lastPrice", 0))
    pe_ltp = safe_int(pe.get("lastPrice", 0))
    
    # CE/PE intrinsic values
    ce_iv = max(spot_price - strike, 0)
    pe_iv = max(strike - spot_price, 0)
    
    # Risk calculations (CE/PE vs own LTP)
    ce_risk = ce_ltp - ce_iv
    pe_risk = pe_ltp - pe_iv
    ce_pe_diff = ce_risk - pe_risk
    
    rows.append({
        "strikePrice": strike,
        "CE_LTP": ce_ltp,
        "CE_%OI": safe_int(ce.get("pchangeinOpenInterest", 0)),
        "CE_Risk": ce_risk,
        "CE_OI": safe_int(ce.get("openInterest", 0)),
        "PE_LTP": pe_ltp,
        "PE_%OI": safe_int(pe.get("pchangeinOpenInterest", 0)),
        "PE_Risk": pe_risk,
        "PE_OI": safe_int(pe.get("openInterest", 0)),
        "CE_PE_Diff": ce_pe_diff
    })

df = pd.DataFrame(rows).drop_duplicates(subset=["strikePrice"]).sort_values("strikePrice").reset_index(drop=True)
if df.empty:
    st.error("No strike data available after parsing.")
    st.stop()

# ----------------- ATM ±6 table -----------------
atm_idx = (df["strikePrice"] - spot_price).abs().idxmin()
window_before = 6
window_after = 6
start_idx = max(0, int(atm_idx) - window_before)
end_idx = min(len(df) - 1, int(atm_idx) + window_after)
df_filtered = df.iloc[start_idx:end_idx + 1].copy().reset_index(drop=True)
atm_strike = df_filtered["strikePrice"].iloc[(df_filtered["strikePrice"] - spot_price).abs().argmin()]

# ----------------- PCR calculations -----------------
total_pe_oi = df_filtered["PE_OI"].sum()
total_ce_oi = df_filtered["CE_OI"].sum()
total_pcr = (total_pe_oi / total_ce_oi) if total_ce_oi != 0 else float("inf")
trend = "🟢 Bullish" if total_pcr > 1 else "🔴 Bearish"

# ATM ±4 PCR (kept as-is)
atm_idx_filtered = df_filtered["strikePrice"].sub(spot_price).abs().idxmin()
start_atm_idx = max(0, int(atm_idx_filtered) - 4)
end_atm_idx = min(len(df_filtered) - 1, int(atm_idx_filtered) + 4)
df_atm_window = df_filtered.iloc[start_atm_idx:end_atm_idx+1]
atm_pe_oi = df_atm_window["PE_OI"].sum()
atm_ce_oi = df_atm_window["CE_OI"].sum()
atm_pcr = (atm_pe_oi / atm_ce_oi) if atm_ce_oi != 0 else float("inf")
atm_trend = "🟢 Bullish" if atm_pcr > 1 else "🔴 Bearish"

# ----------------- Rocket logic -----------------
atm_row = df_filtered[df_filtered["strikePrice"] == atm_strike].iloc[0]
atm_ce_risk = atm_row["CE_Risk"]
atm_pe_risk = atm_row["PE_Risk"]

rocket_symbol = "⚪"
rocket_text = "Neutral"
if (total_pcr > 1) and (atm_pe_oi > atm_ce_oi) and (atm_row["PE_%OI"] > 0):
    rocket_symbol = "🟢🚀"
    rocket_text = "Strong Bullish"
elif (total_pcr < 1) and (atm_ce_oi > atm_pe_oi) and (atm_row["CE_%OI"] > 0):
    rocket_symbol = "🔴🚀"
    rocket_text = "Strong Bearish"
else:
    rocket_symbol = "🤔"
    rocket_text = "Conflict / Wait"

# ----------------- Prepare display dataframe -----------------
max_ce_oi = int(df_filtered["CE_OI"].max())
max_pe_oi = int(df_filtered["PE_OI"].max())
display = df_filtered.copy()
display["Strike"] = display["strikePrice"].apply(lambda s: f"[ATM] {s}" if s == atm_strike else f"{s}")

# ----------------- Styling -----------------
def style_row(row):
    styles = [""] * len(row)
    col_idx = {col: i for i, col in enumerate(display.columns)}

    # Fresh OI coloring
    if row["CE_%OI"] > 0:
        for c in ["CE_LTP","CE_%OI","CE_Risk","CE_OI"]:
            styles[col_idx[c]] = 'background-color: #ffcdd2'
    if row["PE_%OI"] > 0:
        for c in ["PE_LTP","PE_%OI","PE_Risk","PE_OI"]:
            styles[col_idx[c]] = 'background-color: #c8e6c9'

    # Max OI highlight
    if row["CE_OI"] == max_ce_oi:
        styles[col_idx["CE_OI"]] = 'background-color: #e57373; font-weight: bold'
    if row["PE_OI"] == max_pe_oi:
        styles[col_idx["PE_OI"]] = 'background-color: #81c784; font-weight: bold'

    # CE/PE Risk coloring
    if row["CE_Risk"] > 0:
        styles[col_idx["CE_Risk"]] = 'color: green; font-weight: 700'
    elif row["CE_Risk"] < 0:
        styles[col_idx["CE_Risk"]] = 'color: red; font-weight: 700'
    if row["PE_Risk"] > 0:
        styles[col_idx["PE_Risk"]] = 'color: green; font-weight: 700'
    elif row["PE_Risk"] < 0:
        styles[col_idx["PE_Risk"]] = 'color: red; font-weight: 700'

    # CE-PE Risk Diff
    if row["CE_PE_Diff"] > 0:
        styles[col_idx["CE_PE_Diff"]] = 'color: green; font-weight: 700'
    elif row["CE_PE_Diff"] < 0:
        styles[col_idx["CE_PE_Diff"]] = 'color: red; font-weight: 700'

    # ATM strike border
    if str(row["Strike"]).startswith("[ATM]"):
        for i in range(len(styles)):
            styles[i] = (styles[i] + '; background-color: #fff8cc') if styles[i] else 'background-color: #fff8cc'
        styles[col_idx["Strike"]] += '; border: 2px solid #000; font-weight: 700'

    return styles

styled = display.style.apply(style_row, axis=1)

# ----------------- Display -----------------
st.markdown("---")
st.markdown(
    f"**Live Snapshot:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
    f"Spot: {safe_int(spot_price)} | "
    f"PCR (all shown): {total_pcr:.2f} → {trend} | "
    f"PCR (ATM ±4): {atm_pcr:.2f} → {atm_trend} | {rocket_symbol} {rocket_text}"
)

st.write(f"### 🔍 ATM ±6 Strike Option Chain (with CE/PE Risk & CE-PE Diff)")
st.dataframe(styled, use_container_width=True, hide_index=True)

# ----------------- Summary columns -----------------
col1, col2 = st.columns([1,1])
with col1:
    st.write("**📌 Max OI (on displayed strikes)**")
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

# ----------------- Max OI block -----------------
col1,col2 = st.columns([1,1])
with col1:
    st.write("**📌 Max OI (Full expiry)**")
    st.write(f"Max CE OI: {max_ce_row['CE_OI']} | LTP: {max_ce_row['CE_LTP']} | Change%: {max_ce_row['CE_Change%']} | Risk: {max_ce_row['CE_Risk']}")
    st.write(f"Max PE OI: {max_pe_row['PE_OI']} | LTP: {max_pe_row['PE_LTP']} | Change%: {max_pe_row['PE_Change%']} | Risk: {max_pe_row['PE_Risk']}")

with col2:
    st.write("**🟢🔴 Fresh OI summary (Directional)**")
    st.write(f"CE builds (resistance): {ce_builds}")
    st.write(f"PE builds (support): {pe_builds}")
    st.write(f"Both sides unwinding: {both_unwind}")

# ----------------- Bottom ticker -----------------
st.markdown("---")
st.markdown(
    f"**Live Snapshot:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
    f"Spot: {safe_int(spot_price)} | "
    f"PCR (ATM ±4): {round(atm_pcr,2) if atm_pcr!=float('inf') else '∞'}"
)
