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
    # initial visit to set cookies
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

# Manual refresh
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

# Try to find underlying value robustly
underlying_value = records.get("underlyingValue") or raw.get("underlyingValue") or None
if underlying_value is None:
    # look inside CE/PE entries
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

    # intrinsic values (IV) — as per prior discussion
    ce_iv = max(spot_price - strike, 0)    # CE intrinsic
    pe_iv = max(strike - spot_price, 0)    # PE intrinsic

    ce_ltp = safe_int(ce.get("lastPrice", 0))
    pe_ltp = safe_int(pe.get("lastPrice", 0))

    # USER-specified risk formula: CE_Risk = CE_LTP - CE_IV
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

# ----------------- ATM-centric selection (±5 strikes) -----------------
if df.empty:
    st.error("No strike data available after parsing.")
    st.stop()

# find ATM strike index on full df
atm_idx_full = (df["strikePrice"] - spot_price).abs().idxmin()
window_before = 5
window_after = 5
start_idx = max(0, int(atm_idx_full) - window_before)
end_idx = min(len(df) - 1, int(atm_idx_full) + window_after)
df_filtered = df.iloc[start_idx:end_idx + 1].copy().reset_index(drop=True)

# Ensure ascending order (important per your observation)
df_filtered = df_filtered.sort_values("strikePrice").reset_index(drop=True)

# recompute ATM strike after filtering & sorting
atm_idx_filtered = (df_filtered["strikePrice"] - spot_price).abs().idxmin()
atm_strike = int(df_filtered.loc[atm_idx_filtered, "strikePrice"])

# ----------------- PCR calculations -----------------
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

# ATM percentage OI change values for confirmation logic
atm_row = df_filtered.iloc[atm_idx_filtered]
atm_ce_pct = int(atm_row.get("CE_pchgOI", 0))
atm_pe_pct = int(atm_row.get("PE_pchgOI", 0))

# ----------------- Rocket logic (Option B - stronger confirmation) -----------------
rocket_symbol = "⚪"
rocket_text = "Neutral"

if (total_pcr > 1) and (atm_pe_oi > atm_ce_oi) and (atm_pe_pct > 0):
    rocket_symbol = "🟢🚀"
    rocket_text = "Strong Bullish"
elif (total_pcr < 1) and (atm_ce_oi > atm_pe_oi) and (atm_ce_pct > 0):
    rocket_symbol = "🔴🚀"
    rocket_text = "Strong Bearish"
else:
    # partial confirmations
    if (total_pcr > 1 and atm_pe_oi > atm_ce_oi) or (atm_pe_pct > 0 and atm_pe_oi > atm_ce_oi):
        rocket_symbol = "🟡⚠️"
        rocket_text = "Bullish but Risky"
    elif (total_pcr < 1 and atm_ce_oi > atm_pe_oi) or (atm_ce_pct > 0 and atm_ce_oi > atm_pe_oi):
        rocket_symbol = "🟡⚠️"
        rocket_text = "Bearish but Risky"
    else:
        rocket_symbol = "🤔"
        rocket_text = "Conflict / Wait"

# ----------------- Prepare display DataFrame -----------------
display = df_filtered.copy()
display["Strike"] = display["strikePrice"].astype(int)
display = display[["CE_LTP","CE_pchgOI","CE_Risk","CE_OI","Strike","PE_LTP","PE_Risk","PE_pchgOI","PE_OI"]]

# rename percent columns to nicer labels
display = display.rename(columns={
    "CE_pchgOI": "CE_%OI",
    "PE_pchgOI": "PE_%OI"
})

# Ensure integer types for display
for c in ["CE_LTP","CE_%OI","CE_Risk","CE_OI","PE_LTP","PE_%OI","PE_Risk","PE_OI"]:
    if c in display.columns:
        display[c] = display[c].fillna(0).astype(int)

# ----------------- Styling -----------------
max_ce_oi = int(display["CE_OI"].max()) if not display["CE_OI"].empty else 0
max_pe_oi = int(display["PE_OI"].max()) if not display["PE_OI"].empty else 0

def style_row(row):
    styles = [''] * len(row)
    col_idx = {col: i for i, col in enumerate(display.columns)}

    # Fresh OI coloring (CE red shade, PE green shade) - keeps screenshot style
    if int(row["CE_%OI"]) > 0:
        for c in ["CE_LTP","CE_%OI","CE_Risk","CE_OI"]:
            styles[col_idx[c]] = 'background-color: #ffcdd2'  # light red
    if int(row["PE_%OI"]) > 0:
        for c in ["PE_LTP","PE_%OI","PE_Risk","PE_OI"]:
            styles[col_idx[c]] = 'background-color: #c8e6c9'  # light green

    # Max OI emphasis
    if int(row["CE_OI"]) == max_ce_oi and max_ce_oi > 0:
        styles[col_idx["CE_OI"]] = 'background-color: #e57373; font-weight: bold'
    if int(row["PE_OI"]) == max_pe_oi and max_pe_oi > 0:
        styles[col_idx["PE_OI"]] = 'background-color: #81c784; font-weight: bold'

    # Signed Risk colors (green when positive, red when negative)
    for col in ["CE_Risk", "PE_Risk"]:
        val = int(row[col])
        base = styles[col_idx[col]]
        if val > 0:
            styles[col_idx[col]] = base + '; color: green'
        elif val < 0:
            styles[col_idx[col]] = base + '; color: red'
        else:
            styles[col_idx[col]] = base + '; color: black'

    # Entire ATM row highlight (light glow + border)
    try:
        if int(row["Strike"]) == atm_strike:
            # apply background to ALL cells in ATM row plus bold and border on Strike cell
            for i in range(len(styles)):
                styles[i] = (styles[i] + '; background-color: #f5f5f5') if styles[i] else 'background-color: #f5f5f5'
            # add a stronger border and bold to Strike
            styles[col_idx["Strike"]] += '; border: 2px solid #000; font-weight: 700'
    except Exception:
        pass

    return styles

styled = display.style.apply(style_row, axis=1)

# ----------------- Bottom ticker -----------------
st.markdown("---")
pcr_display = (f"{total_pcr:.2f}" if total_pcr != float("inf") else "∞")
atm_pcr_display = (f"{atm_pcr:.2f}" if atm_pcr != float("inf") else "∞")
st.markdown(
    f"**Live Snapshot:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
    f"Spot: {safe_int(spot_price)} | "
    f"PCR (all shown): {pcr_display} → {trend} | "
    f"PCR (ATM ±4): {atm_pcr_display} → {atm_trend} | {rocket_symbol} {rocket_text}"
)

# ----------------- Display table -----------------
st.write("### 🔍 ATM ±5 Strike Option Chain (ascending strikes)")
# Streamlit can render a pandas Styler; use st.dataframe for interactivity
try:
    st.dataframe(styled, use_container_width=True, hide_index=True)
except Exception:
    # fallback to HTML if needed
    st.write(styled.to_html(), unsafe_allow_html=True)
