# filename: pages_Option_Chain_Full.py
import requests
import pandas as pd
import streamlit as st
from datetime import datetime, timedelta
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

def safe_float(x):
    try:
        return round(float(x), 1)
    except:
        return 0.0

# ----------------- UI -----------------
st.title("📊 NIFTY / BANKNIFTY Option Chain — Full OI Tracker")
symbol = st.radio("Select Index", ["NIFTY", "BANKNIFTY"], horizontal=True)

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

spot_price = safe_float(underlying_value)

# ----------------- Build DataFrame -----------------
rows = []
for r in filtered_rows:
    strike = safe_float(r.get("strikePrice", 0))
    ce = r.get("CE") or {}
    pe = r.get("PE") or {}

    ce_ltp = safe_float(ce.get("lastPrice", 0))
    pe_ltp = safe_float(pe.get("lastPrice", 0))

    ce_iv = max(spot_price - strike,0)
    pe_iv = max(strike - spot_price,0)

    ce_risk = safe_float(ce_ltp - ce_iv)
    pe_risk = safe_float(pe_ltp - pe_iv)
    ce_pe_diff = safe_float(ce_risk - pe_risk)

    rows.append({
        "strikePrice": strike,
        "CE_LTP": ce_ltp,
        "CE_%OI": safe_float(ce.get("pchangeinOpenInterest",0)),
        "CE_Risk": ce_risk,
        "CE_OI": safe_float(ce.get("openInterest",0)),
        "PE_LTP": pe_ltp,
        "PE_%OI": safe_float(pe.get("pchangeinOpenInterest",0)),
        "PE_Risk": pe_risk,
        "PE_OI": safe_float(pe.get("openInterest",0)),
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
start_idx = max(0,int(atm_idx)-window_before)
end_idx = min(len(df)-1,int(atm_idx)+window_after)
df_filtered = df.iloc[start_idx:end_idx+1].copy().reset_index(drop=True)
atm_strike = df_filtered["strikePrice"].iloc[(df_filtered["strikePrice"]-spot_price).abs().argmin()]

# ----------------- PCR -----------------
total_pe_oi = df_filtered["PE_OI"].sum()
total_ce_oi = df_filtered["CE_OI"].sum()
total_pcr = (total_pe_oi/total_ce_oi) if total_ce_oi!=0 else float("inf")
trend = "🟢 Bullish" if total_pcr>1 else "🔴 Bearish"

atm_idx_filtered = df_filtered["strikePrice"].sub(spot_price).abs().idxmin()
start_atm_idx = max(0,int(atm_idx_filtered)-4)
end_atm_idx = min(len(df_filtered)-1,int(atm_idx_filtered)+4)
df_atm_window = df_filtered.iloc[start_atm_idx:end_atm_idx+1]
atm_pe_oi = df_atm_window["PE_OI"].sum()
atm_ce_oi = df_atm_window["CE_OI"].sum()
atm_pcr = (atm_pe_oi/atm_ce_oi) if atm_ce_oi!=0 else float("inf")
atm_trend = "🟢 Bullish" if atm_pcr>1 else "🔴 Bearish"

# ----------------- Rocket logic -----------------
atm_row = df_filtered[df_filtered["strikePrice"]==atm_strike].iloc[0]
rocket_symbol = "⚪"
rocket_text = "Neutral"
if (total_pcr>1) and (atm_pe_oi>atm_ce_oi) and (atm_row["PE_%OI"]>0):
    rocket_symbol="🟢🚀"
    rocket_text="Strong Bullish"
elif (total_pcr<1) and (atm_ce_oi>atm_pe_oi) and (atm_row["CE_%OI"]>0):
    rocket_symbol="🔴🚀"
    rocket_text="Strong Bearish"
else:
    rocket_symbol="🤔"
    rocket_text="Conflict / Wait"

# ----------------- Display table -----------------
display = df_filtered.copy()
display["Strike"] = display["strikePrice"].apply(lambda s: f"[ATM] {s}" if s==atm_strike else f"{s}")

# Reorder columns as requested
display = display[["CE_OI","CE_%OI","CE_Risk","CE_PE_Diff","CE_LTP","Strike","PE_LTP","PE_Risk","PE_%OI","PE_OI"]]

# Styling
max_ce_oi = df_filtered["CE_OI"].max()
max_pe_oi = df_filtered["PE_OI"].max()
def style_row(row):
    styles=[""]*len(row)
    col_idx = {col:i for i,col in enumerate(display.columns)}

    # Fresh OI
    if row["CE_%OI"]>0:
        for c in ["CE_OI","CE_%OI","CE_Risk","CE_PE_Diff","CE_LTP"]:
            styles[col_idx[c]]='background-color:#ffcdd2'
    if row["PE_%OI"]>0:
        for c in ["PE_OI","PE_%OI","PE_Risk","PE_LTP"]:
            styles[col_idx[c]]='background-color:#c8e6c9'

    # Max OI highlight
    if row["CE_OI"]==max_ce_oi:
        styles[col_idx["CE_OI"]]='background-color:#e57373;font-weight:700'
    if row["PE_OI"]==max_pe_oi:
        styles[col_idx["PE_OI"]]='background-color:#81c784;font-weight:700'

    # Risk colors
    if row["CE_Risk"]>0: styles[col_idx["CE_Risk"]]='color:green;font-weight:700'
    elif row["CE_Risk"]<0: styles[col_idx["CE_Risk"]]='color:red;font-weight:700'
    if row["PE_Risk"]>0: styles[col_idx["PE_Risk"]]='color:green;font-weight:700'
    elif row["PE_Risk"]<0: styles[col_idx["PE_Risk"]]='color:red;font-weight:700'
    if row["CE_PE_Diff"]>0: styles[col_idx["CE_PE_Diff"]]='color:green;font-weight:700'
    elif row["CE_PE_Diff"]<0: styles[col_idx["CE_PE_Diff"]]='color:red;font-weight:700'

    # ATM strike
    if str(row["Strike"]).startswith("[ATM]"):
        for i in range(len(styles)):
            styles[i] = (styles[i]+'; background-color:#fff8cc') if styles[i] else 'background-color:#fff8cc'
        styles[col_idx["Strike"]]+='; border:2px solid #000;font-weight:700'

    return styles

styled = display.style.apply(style_row, axis=1)

# ----------------- Display -----------------
ist_now = datetime.utcnow() + timedelta(hours=5, minutes=30)  # convert UTC → IST
st.markdown("---")
st.markdown(
    f"**Live Snapshot (IST):** {ist_now.strftime('%Y-%m-%d %H:%M:%S')} | "
    f"Spot: {spot_price:.1f} | PCR (all shown): {total_pcr:.2f} → {trend} | "
    f"PCR (ATM ±4): {atm_pcr:.1f} → {atm_trend} | {rocket_symbol} {rocket_text}"
)

st.write("### 🔍 ATM ±6 Strike Option Chain (with CE/PE Risk & CE-PE Diff)")
st.dataframe(styled,use_container_width=True, hide_index=True)

# ----------------- Max OI history chart -----------------
if "max_oi_history" not in st.session_state:
    st.session_state.max_oi_history = []

# IST timestamp
timestamp = (datetime.utcnow() + timedelta(hours=5, minutes=30)).strftime("%H:%M:%S")

max_ce_oi_strike = df["CE_OI"].idxmax() if not df.empty else 0
max_ce_pct_strike = df["CE_%OI"].idxmax() if not df.empty else 0
max_pe_oi_strike = df["PE_OI"].idxmax() if not df.empty else 0
max_pe_pct_strike = df["PE_%OI"].idxmax() if not df.empty else 0

st.session_state.max_oi_history.append({
    "time":timestamp,
    "Max_CE_OI":df.loc[max_ce_oi_strike,"strikePrice"] if not df.empty else 0,
    "Max_CE_%OI":df.loc[max_ce_pct_strike,"strikePrice"] if not df.empty else 0,
    "Max_PE_OI":df.loc[max_pe_oi_strike,"strikePrice"] if not df.empty else 0,
    "Max_PE_%OI":df.loc[max_pe_pct_strike,"strikePrice"] if not df.empty else 0,
})

st.session_state.max_oi_history = st.session_state.max_oi_history[-20:]
hist_df = pd.DataFrame(st.session_state.max_oi_history).set_index("time")

# Adjust y-axis scale to 2 strikes below PE support strike
pe_support_strike = df["strikePrice"].iloc[df["PE_OI"].idxmax()]
strike_step = 50 if symbol=="NIFTY" else 100
min_y = pe_support_strike - 2*strike_step
max_y = df["strikePrice"].max()

st.write("### 📈 Max CE/PE OI & %OI Strike Evolution (Last 20 snapshots)")
st.line_chart(hist_df, use_container_width=True)
