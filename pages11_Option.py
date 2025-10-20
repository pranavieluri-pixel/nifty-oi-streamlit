# filename: pages5_Option_Chain_OI_Tracker.py
import requests
import pandas as pd
import streamlit as st
from datetime import datetime
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="NIFTY & BANKNIFTY Option Chain - OI Tracker", layout="wide")

# ----------------- Auto-refresh (every 1 second) -----------------
# This returns how many times the app has been re-run by the autorefresh.
# We don't need the value here, but calling it makes the page auto-refresh.
_ = st_autorefresh(interval=1000, limit=None, key="auto_refresh")  # 1000 ms = 1s

# ----------------- Helpers -----------------
def fetch_option_chain(symbol):
    url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/"
    }
    s = requests.Session()
    # preflight (NSE sometimes requires the base to be requested first)
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

# underlying spot fallback logic
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
    ce_iv = max(spot_price - strike, 0)
    pe_iv = max(strike - spot_price, 0)
    # last price of opposite side used in risk calc
    ce_last = safe_int(ce.get("lastPrice", 0))
    pe_last = safe_int(pe.get("lastPrice", 0))
    ce_risk = safe_int(ce_iv - pe_last)  # CE_Risk = IV(CE) - PE_LTP
    pe_risk = safe_int(pe_iv - ce_last)  # PE_Risk = IV(PE) - CE_LTP
    rows.append({
        "strikePrice": strike,
        "CE_LTP": ce_last,
        "CE_%OI": safe_int(ce.get("pchangeinOpenInterest", 0)),
        "CE_Risk": ce_risk,
        "CE_OI": safe_int(ce.get("openInterest", 0)),
        "PE_LTP": pe_last,
        "PE_%OI": safe_int(pe.get("pchangeinOpenInterest", 0)),
        "PE_Risk": pe_risk,
        "PE_OI": safe_int(pe.get("openInterest", 0))
    })

df = pd.DataFrame(rows).drop_duplicates(subset=["strikePrice"]).sort_values("strikePrice").reset_index(drop=True)

# ----------------- ATM-centric selection (±5 strikes) -----------------
if df.empty:
    st.error("No strike data available after parsing.")
    st.stop()

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

# ATM ±4 PCR
atm_idx_filtered = df_filtered["strikePrice"].sub(spot_price).abs().idxmin()
start_atm_idx = max(0, int(atm_idx_filtered) - 4)
end_atm_idx = min(len(df_filtered) - 1, int(atm_idx_filtered) + 4)
df_atm_window = df_filtered.iloc[start_atm_idx:end_atm_idx+1]
atm_pe_oi = df_atm_window["PE_OI"].sum()
atm_ce_oi = df_atm_window["CE_OI"].sum()
atm_pcr = (atm_pe_oi / atm_ce_oi) if atm_ce_oi != 0 else float("inf")
atm_trend = "🟢 Bullish" if atm_pcr > 1 else "🔴 Bearish"

# ATM ±5 PCR (for comparison)
start_atm_idx_5 = max(0, int(atm_idx_filtered) - 5)
end_atm_idx_5 = min(len(df_filtered) - 1, int(atm_idx_filtered) + 5)
df_atm_window_5 = df_filtered.iloc[start_atm_idx_5:end_atm_idx_5+1]
atm5_pe_oi = df_atm_window_5["PE_OI"].sum()
atm5_ce_oi = df_atm_window_5["CE_OI"].sum()
atm5_pcr = (atm5_pe_oi / atm5_ce_oi) if atm5_ce_oi != 0 else float("inf")
atm5_trend = "🟢 Bullish" if atm5_pcr > 1 else "🔴 Bearish"

# ----------------- Determine rocket symbol -----------------
atm_row = df_filtered[df_filtered["strikePrice"] == atm_strike].iloc[0]
atm_ce_risk = atm_row["CE_Risk"]
atm_pe_risk = atm_row["PE_Risk"]

# Final symbol rules:
# - Strong bullish match => green rocket 🚀
# - Strong bearish match => fiery rocket 🔥🚀 (we use 🔥 for visibility)
# - Any mismatch or neutral => thinking 🤔
rocket_symbol = "🤔"
if total_pcr > 1 and atm_ce_risk > atm_pe_risk:
    rocket_symbol = "🚀"
elif total_pcr < 1 and atm_pe_risk > atm_ce_risk:
    rocket_symbol = "🔥🚀"
else:
    rocket_symbol = "🤔"

# ----------------- Prepare display dataframe -----------------
max_ce_oi = int(df_filtered["CE_OI"].max())
max_pe_oi = int(df_filtered["PE_OI"].max())

display = df_filtered.copy()
display["Strike"] = display["strikePrice"].apply(lambda s: f"【ATM】 {s}" if s == atm_strike else f"{s}")

# reorder ATM-centric view: lower (descending), ATM, upper (ascending)
atm_pos = display.index[display["strikePrice"] == atm_strike][0]
lower = display.iloc[:atm_pos].copy().iloc[::-1].reset_index(drop=True)
atm_row_df = display.iloc[[atm_pos]].copy().reset_index(drop=True)
upper = display.iloc[atm_pos+1:].copy().reset_index(drop=True)
display_df = pd.concat([lower, atm_row_df, upper], ignore_index=True)

show_cols = [
    "CE_LTP", "CE_%OI", "CE_Risk", "CE_OI",
    "Strike",
    "PE_OI", "PE_Risk", "PE_%OI", "PE_LTP"
]
display_df = display_df[show_cols].rename(columns={
    "CE_LTP": "CE_LTP",
    "CE_%OI": "CE_%OI",
    "CE_Risk": "CE_Risk",
    "CE_OI": "CE_OI",
    "PE_LTP": "PE_LTP",
    "PE_%OI": "PE_%OI",
    "PE_Risk": "PE_Risk",
    "PE_OI": "PE_OI"
})

for c in ["CE_LTP","CE_%OI","CE_Risk","CE_OI","PE_LTP","PE_%OI","PE_Risk","PE_OI"]:
    display_df[c] = display_df[c].fillna(0).astype(int)

# ----------------- Styling -----------------
def style_row(row):
    styles = [""] * len(row)
    col_idx = {col: i for i, col in enumerate(display_df.columns)}
    ce_pchg = int(row["CE_%OI"])
    pe_pchg = int(row["PE_%OI"])

    # Fresh OI coloring
    if ce_pchg > 0:
        for c in ["CE_LTP", "CE_%OI", "CE_Risk", "CE_OI"]:
            styles[col_idx[c]] = 'background-color: #ffcdd2'
    if pe_pchg > 0:
        for c in ["PE_LTP", "PE_%OI", "PE_Risk", "PE_OI"]:
            styles[col_idx[c]] = 'background-color: #c8e6c9'

    # Max OI highlight
    if row["CE_OI"] == max_ce_oi:
        styles[col_idx["CE_OI"]] = 'background-color: #e57373; font-weight: bold'
    if row["PE_OI"] == max_pe_oi:
        styles[col_idx["PE_OI"]] = 'background-color: #81c784; font-weight: bold'

    # ATM border
    try:
        strike_val = row["Strike"]
        if isinstance(strike_val, str) and strike_val.startswith("【ATM】"):
            # ensure we don't blow up if empty
            styles[col_idx["Strike"]] = (styles[col_idx["Strike"]] + '; border: 2px solid #000; font-weight: 700').lstrip(';')
    except Exception:
        pass

    # Signed Risk colors
    for col in ["CE_Risk", "PE_Risk"]:
        try:
            val = int(row[col])
        except Exception:
            val = 0
        if val > 0:
            styles[col_idx[col]] = (styles[col_idx[col]] + '; color: green').lstrip(';')
        elif val < 0:
            styles[col_idx[col]] = (styles[col_idx[col]] + '; color: red').lstrip(';')
        else:
            styles[col_idx[col]] = (styles[col_idx[col]] + '; color: black').lstrip(';')

    return styles

styled = display_df.style.apply(style_row, axis=1)

# ----------------- Top PCR display -----------------
st.markdown(f"### 🧭 Spot: **{safe_int(spot_price)}** ({symbol})")
st.markdown(f"**PCR (Put/Call Ratio on displayed strikes): {round(total_pcr,2) if total_pcr!=float('inf') else '∞'} → {trend} {rocket_symbol}**")
st.markdown(f"**PCR (ATM ±4 strikes): {round(atm_pcr,2) if atm_pcr!=float('inf') else '∞'} → {atm_trend}**")
st.markdown(f"**PCR (ATM ±5 strikes): {round(atm5_pcr,2) if atm5_pcr!=float('inf') else '∞'} → {atm5_trend}**")

# ----------------- Display table -----------------
st.write(f"### 🔍 ATM ±5 Strike Option Chain")
st.dataframe(styled, use_container_width=True, hide_index=True)

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
    f"PCR (all shown): {round(total_pcr,2) if total_pcr!=float('inf') else '∞'} → {trend} | "
    f"PCR (ATM ±4): {round(atm_pcr,2) if atm_pcr!=float('inf') else '∞'} → {atm_trend} | "
    f"PCR (ATM ±5): {round(atm5_pcr,2) if atm5_pcr!=float('inf') else '∞'} → {atm5_trend} | "
    f"{rocket_symbol}"
)
