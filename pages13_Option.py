# filename: pages_Option_Chain_OI_Tracker.py
import requests
import pandas as pd
import streamlit as st
from datetime import datetime
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="NIFTY & BANKNIFTY Option Chain - OI Tracker", layout="wide")

# ----------------- Auto-refresh (every 30 seconds) -----------------
_ = st_autorefresh(interval=30000, limit=None, key="auto_refresh")  # 30000 ms = 30s

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
    except:
        return 0

# ----------------- UI -----------------
st.title("📊 NIFTY / BANKNIFTY Option Chain — OI Tracker")
symbol = st.radio("Select Index", ["NIFTY", "BANKNIFTY"], horizontal=True)

# ----------------- Fetch data -----------------
raw = fetch_option_chain(symbol)

records = raw.get("records") or {}
expiry_dates = records.get("expiryDates") or []
data_list = records.get("data") or raw.get("filtered", {}).get("data") or raw.get("data") or []

# underlying value
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

spot_price = float(underlying_value) if underlying_value else 0.0

# ----------------- Build full DataFrame -----------------
rows = []
for r in filtered_rows:
    strike = safe_int(r.get("strikePrice", 0))
    ce = r.get("CE") or {}
    pe = r.get("PE") or {}

    ce_ltp = safe_int(ce.get("lastPrice", 0))
    ce_oi = safe_int(ce.get("openInterest", 0))
    ce_oi_chg = safe_int(ce.get("changeinOpenInterest", 0))
    ce_prev_oi = ce_oi - ce_oi_chg
    ce_oi_pct = safe_int((ce_oi_chg / ce_prev_oi * 100) if ce_prev_oi > 0 else 0)
    ce_risk = safe_int(ce_ltp - max(spot_price - strike, 0))

    pe_ltp = safe_int(pe.get("lastPrice", 0))
    pe_oi = safe_int(pe.get("openInterest", 0))
    pe_oi_chg = safe_int(pe.get("changeinOpenInterest", 0))
    pe_prev_oi = pe_oi - pe_oi_chg
    pe_oi_pct = safe_int((pe_oi_chg / pe_prev_oi * 100) if pe_prev_oi > 0 else 0)
    pe_risk = safe_int(pe_ltp - max(strike - spot_price, 0))

    rows.append({
        "strikePrice": strike,
        "CE_LTP": ce_ltp,
        "CE_OI": ce_oi,
        "CE_Change%": ce_oi_pct,
        "CE_Risk": ce_risk,
        "PE_LTP": pe_ltp,
        "PE_OI": pe_oi,
        "PE_Change%": pe_oi_pct,
        "PE_Risk": pe_risk
    })

df = pd.DataFrame(rows).sort_values("strikePrice").reset_index(drop=True)

# ----------------- ATM ±6 strikes display -----------------
atm_idx = (df["strikePrice"] - spot_price).abs().idxmin()
start_idx = max(0, int(atm_idx)-6)
end_idx = min(len(df)-1, int(atm_idx)+6)
df_display = df.iloc[start_idx:end_idx+1].copy().reset_index(drop=True)
atm_strike = df_display["strikePrice"].iloc[(df_display["strikePrice"] - spot_price).abs().argmin()]

# ----------------- PCR (ATM ±4) -----------------
start_idx4 = max(0, int(atm_idx)-4)
end_idx4 = min(len(df_display)-1, int(atm_idx)+4)
atm_window = df_display.iloc[start_idx4:end_idx4+1]
atm_ce_oi = atm_window["CE_OI"].sum()
atm_pe_oi = atm_window["PE_OI"].sum()
atm_pcr = (atm_pe_oi / atm_ce_oi) if atm_ce_oi>0 else float("inf")
atm_trend = "🟢 Bullish" if atm_pcr>1 else "🔴 Bearish"

# ----------------- Full expiry Max OI -----------------
max_ce_row = df.loc[df["CE_OI"].idxmax()]
max_pe_row = df.loc[df["PE_OI"].idxmax()]

# ----------------- Fresh OI summary -----------------
ce_builds = int((df_display["CE_Change%"]>0).sum())
pe_builds = int((df_display["PE_Change%"]>0).sum())
both_unwind = int(((df_display["CE_Change%"]<0) & (df_display["PE_Change%"]<0)).sum())

# ----------------- Display table -----------------
display_df = df_display.copy()
display_df["Strike"] = display_df["strikePrice"].apply(lambda s: f"【ATM】 {s}" if s==atm_strike else str(s))

show_cols = ["CE_LTP","CE_OI","CE_Change%","CE_Risk",
             "Strike",
             "PE_Risk","PE_Change%","PE_OI","PE_LTP"]

display_df = display_df[show_cols]

def style_row(row):
    styles = [""]*len(row)
    col_idx = {c:i for i,c in enumerate(display_df.columns)}

    # CE coloring
    if row["CE_Change%"]>0:
        for c in ["CE_LTP","CE_OI","CE_Change%","CE_Risk"]:
            styles[col_idx[c]]='background-color: #ffcdd2'
    # PE coloring
    if row["PE_Change%"]>0:
        for c in ["PE_LTP","PE_OI","PE_Change%","PE_Risk"]:
            styles[col_idx[c]]='background-color: #c8e6c9'

    # ATM strike bold
    if str(row["Strike"]).startswith("【ATM】"):
        for i in range(len(styles)):
            styles[i]+='; font-weight: 700; border: 2px solid #000'

    # Risk colors
    for col in ["CE_Risk","PE_Risk"]:
        if row[col]>0:
            styles[col_idx[col]+0]=styles[col_idx[col]]+'; color: green'
        elif row[col]<0:
            styles[col_idx[col]+0]=styles[col_idx[col]]+'; color: red'
        else:
            styles[col_idx[col]+0]=styles[col_idx[col]]+'; color: black'
    return styles

styled = display_df.style.apply(style_row, axis=1)

# ----------------- Top PCR & Spot -----------------
st.markdown(f"### 🧭 Spot: **{safe_int(spot_price)}** ({symbol})")
st.markdown(f"**PCR (ATM ±4 strikes): {round(atm_pcr,2) if atm_pcr!=float('inf') else '∞'} → {atm_trend}**")

# ----------------- Display table -----------------
st.write("### 🔍 ATM ±6 Strike Option Chain")
st.dataframe(styled,use_container_width=True,hide_index=True)

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
