import requests
import pandas as pd
import streamlit as st
from datetime import datetime

st.set_page_config(page_title="NIFTY & BANKNIFTY Option Chain", layout="wide")

# ----------------- FUNCTION TO FETCH DATA -----------------
@st.cache_data(ttl=60)
def get_option_chain(symbol):
    url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/",
    }
    session = requests.Session()
    response = session.get(url, headers=headers)
    data = response.json()
    return data

# ----------------- MAIN APP -----------------
st.title("📊 NIFTY / BANKNIFTY Option Chain - OI Tracker")

symbol = st.radio("Select Index", ["NIFTY", "BANKNIFTY"], horizontal=True)
data = get_option_chain(symbol)

records = data["records"]["data"]
spot_price = data["records"]["underlyingValue"]

st.markdown(f"### 🧭 Spot: **{spot_price:.2f}** ({symbol})")

# Flatten data
rows = []
for r in records:
    if "CE" in r and "PE" in r:
        rows.append({
            "strikePrice": r["strikePrice"],
            "CE_OI": r["CE"]["openInterest"],
            "CE_ChangeOI": r["CE"]["changeinOpenInterest"],
            "CE_LTP": r["CE"]["lastPrice"],
            "PE_OI": r["PE"]["openInterest"],
            "PE_ChangeOI": r["PE"]["changeinOpenInterest"],
            "PE_LTP": r["PE"]["lastPrice"]
        })

df = pd.DataFrame(rows)

# Find ATM and filter ±5 strikes
atm_strike = min(df["strikePrice"], key=lambda x: abs(x - spot_price))
df_filtered = df[(df["strikePrice"] >= atm_strike - 5 * 100) &
                 (df["strikePrice"] <= atm_strike + 5 * 100)].copy()

# PCR Calculation
total_pcr = df_filtered["PE_OI"].sum() / df_filtered["CE_OI"].sum()
trend = "🟢 Bullish" if total_pcr > 1 else "🔴 Bearish"

st.markdown(f"**PCR (Put/Call Ratio): {total_pcr:.2f} → {trend}**")

# Calculate intrinsic differences for ATM row
atm_idx = df_filtered.index[df_filtered["strikePrice"] == atm_strike][0]
atm_row = df_filtered.loc[atm_idx]
call_intrinsic = max(0, spot_price - atm_strike)
put_intrinsic = max(0, atm_strike - spot_price)
call_diff = call_intrinsic - atm_row["PE_LTP"]
put_diff = put_intrinsic - atm_row["CE_LTP"]

# Add columns for Intrinsic comparison (empty except ATM)
df_filtered["CE_Intrinsic_vs_PE"] = ""
df_filtered["PE_Intrinsic_vs_CE"] = ""

if call_diff > 0:
    df_filtered.at[atm_idx, "CE_Intrinsic_vs_PE"] = f"🟢 +₹{call_diff:.2f}"
if put_diff > 0:
    df_filtered.at[atm_idx, "PE_Intrinsic_vs_CE"] = f"🟢 +₹{put_diff:.2f}"

# Highlight highest OI strikes
max_ce_oi = df_filtered["CE_OI"].max()
max_pe_oi = df_filtered["PE_OI"].max()

def highlight_row(row):
    if row["CE_OI"] == max_ce_oi:
        return ['background-color: #ffcdd2']*len(row)
    elif row["PE_OI"] == max_pe_oi:
        return ['background-color: #c8e6c9']*len(row)
    elif row["strikePrice"] == atm_strike:
        return ['background-color: #fff9c4']*len(row)
    return ['']*len(row)

# Display table
st.write("### 🔍 Current Week Option Chain (±5 Strikes from ATM)")

styled_df = df_filtered[[
    "CE_OI", "CE_ChangeOI", "CE_LTP", "CE_Intrinsic_vs_PE",
    "strikePrice",
    "PE_Intrinsic_vs_CE", "PE_LTP", "PE_ChangeOI", "PE_OI"
]].style.apply(highlight_row, axis=1)

st.dataframe(styled_df, use_container_width=True, hide_index=True)

# ----------------- BAR CHARTS -----------------
st.write("### 📈 OI Distribution (Change in OI)")
col1, col2 = st.columns(2)
with col1:
    st.bar_chart(df_filtered.set_index("strikePrice")["CE_ChangeOI"])
with col2:
    st.bar_chart(df_filtered.set_index("strikePrice")["PE_ChangeOI"])

# ----------------- REFRESH BUTTON -----------------
if st.button("🔄 Refresh Data"):
    st.cache_data.clear()
    st.rerun()
