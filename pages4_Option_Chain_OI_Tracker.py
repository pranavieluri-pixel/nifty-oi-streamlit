import requests
import pandas as pd
import streamlit as st
from datetime import datetime

st.set_page_config(page_title="NIFTY & BANKNIFTY Option Chain - OI Tracker", layout="wide")

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

# ----------------- Auto-refresh -----------------
refresh_interval_ms = 1000  # 1 second
st.experimental_rerun()

# ----------------- UI -----------------
st.title("📊 NIFTY / BANKNIFTY Option Chain — OI Tracker")
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

underlying_value = records.get("underlyingValue") or raw.get("underlyingValue") or raw.get("records", {}).get("underlyingValue")
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
    # intrinsic value
    ce_iv = max(spot_price - strike, 0)
    pe_iv = max(strike - spot_price, 0)
    ce_risk = safe_int(ce_iv - safe_int(pe.get("lastPrice", 0)))
    pe_risk = safe_int(pe_iv - safe_int(ce.get("lastPrice", 0)))
    rows.append({
        "strikePrice": strike,
        "CE_OI": safe_int(ce.get("openInterest", 0)),
        "CE_pchgOI": safe_int(ce.get("pchangeinOpenInterest", 0)),
        "CE_LTP": safe_int(ce.get("lastPrice", 0)),
        "CE_Risk": ce_risk,
        "PE_LTP": safe_int(pe.get("lastPrice", 0)),
        "PE_pchgOI": safe_int(pe.get("pchangeinOpenInterest", 0)),
        "PE_OI": safe_int(pe.get("openInterest", 0)),
        "PE_Risk": pe_risk
    })

df = pd.DataFrame(rows).drop_duplicates(subset=["strikePrice"]).sort_values("strikePrice").reset_index(drop=True)

# ----------------- ATM-centric selection (±5 strikes) -----------------
atm_idx = (df["strikePrice"] - spot_price).abs().idxmin()
window_before = 5
window_after = 5
start_idx = max(0, int(atm_idx) - window_before)
end_idx = min(len(df) - 1, int(atm_idx) + window_after)
df_filtered = df.iloc[start_idx:end_idx + 1].copy().reset_index(drop=True)
atm_strike = df_filtered["strikePrice"].iloc[(df_filtered["strikePrice"] - spot_price).abs().argmin()]

# ----------------- PCR calculations -----------------
# Full PCR
total_pe_oi = df_filtered["PE_OI"].sum()
total_ce_oi = df_filtered["CE_OI"].sum()
total_pcr = (total_pe_oi / total_ce_oi) if total_ce_oi != 0 else float("inf")
trend = "🟢 Bullish" if total_pcr > 1 else "🔴 Bearish"

# PCR for ATM ±4 strikes
atm_idx_filtered = df_filtered["strikePrice"].sub(spot_price).abs().idxmin()
start_atm_idx = max(0, int(atm_idx_filtered) - 4)
end_atm_idx = min(len(df_filtered) - 1, int(atm_idx_filtered) + 4)
df_atm_window = df_filtered.iloc[start_atm_idx:end_atm_idx+1]

atm_pe_oi = df_atm_window["PE_OI"].sum()
atm_ce_oi = df_atm_window["CE_OI"].sum()
atm_pcr = (atm_pe_oi / atm_ce_oi) if atm_ce_oi != 0 else float("inf")
atm_trend = "🟢 Bullish" if atm_pcr > 1 else "🔴 Bearish"

# PCR for ±5 strikes
start_atm_idx_5 = max(0, int(atm_idx_filtered) - 5)
end_atm_idx_5 = min(len(df_filtered) - 1, int(atm_idx_filtered) + 5)
df_atm_window_5 = df_filtered.iloc[start_atm_idx_5:end_atm_idx_5+1]

atm5_pe_oi = df_atm_window_5["PE_OI"].sum()
atm5_ce_oi = df_atm_window_5["CE_OI"].sum()
atm5_pcr = (atm5_pe_oi / atm5_ce_oi) if atm5_ce_oi != 0 else float("inf")
atm5_trend = "🟢 Bullish" if atm5_pcr > 1 else "🔴 Bearish"

# ----------------- Determine rocket symbol -----------------
atm_row = df_filtered[df_filtered["strikePrice"]==atm_strike].iloc[0]
atm_ce_risk = atm_row["CE_Risk"]
atm_pe_risk = atm_row["PE_Risk"]

rocket_symbol = "🤔"
if total_pcr > 1 and atm_ce_risk > atm_pe_risk:
    rocket_symbol = "🚀"
elif total_pcr < 1 and atm_pe_risk > atm_ce_risk:
    rocket_symbol = "🔥🚀"

# ----------------- Display table -----------------
max_ce_oi = int(df_filtered["CE_OI"].max())
max_pe_oi = int(df_filtered["PE_OI"].max())

display = df_filtered.copy()
display["strikeLabel"] = display["strikePrice"].apply(lambda s: f"【ATM】 {s}" if s == atm_strike else f"{s}")

atm_pos = display.index[display["strikePrice"] == atm_strike][0]
lower = display.iloc[:atm_pos].copy().iloc[::-1].reset_index(drop=True)
atm_row_df = display.iloc[[atm_pos]].copy().reset_index(drop=True)
upper = display.iloc[atm_pos+1:].copy().reset_index(drop=True)
display_df = pd.concat([lower, atm_row_df, upper], ignore_index=True)

show_cols = [
    "CE_LTP", "CE_pchgOI", "CE_Risk", "CE_OI",
    "strikeLabel",
    "PE_OI", "PE_Risk", "PE_pchgOI", "PE_LTP"
]
display_df = display_df[show_cols].rename(columns={
    "CE_LTP": "CE_LTP",
    "CE_pchgOI": "CE_%OI",
    "CE_Risk": "CE_Risk",
    "CE_OI": "CE_OI",
    "strikeLabel": "Strike",
    "PE_LTP": "PE_LTP",
    "PE_pchgOI": "PE_%OI",
    "PE_Risk": "PE_Risk",
    "PE_OI": "PE_OI"
})

for c in ["CE_LTP","CE_%OI","CE_Risk","CE_OI","PE_LTP","PE_%OI","PE_Risk","PE_OI"]:
    display_df[c] = display_df[c].fillna(0).astype
