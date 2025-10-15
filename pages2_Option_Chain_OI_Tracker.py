import requests
import pandas as pd
import streamlit as st

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
spot_price = float(data["records"]["underlyingValue"])

st.markdown(f"### 🧭 Spot: **{spot_price:.1f}** ({symbol})")

# Flatten data
rows = []
for r in records:
    if "CE" in r and "PE" in r:
        rows.append({
            "strikePrice": float(r["strikePrice"]),
            "CE_OI": r["CE"]["openInterest"],
            "CE_LTP": float(r["CE"]["lastPrice"]),
            "PE_OI": r["PE"]["openInterest"],
            "PE_LTP": float(r["PE"]["lastPrice"])
        })

df = pd.DataFrame(rows)

# ----------------- CALCULATE % CHANGE -----------------
df["CE_%ChangeOI"] = ((df["CE_OI"].diff().fillna(0) / df["CE_OI"].shift(1).fillna(df["CE_OI"])) * 100)
df["PE_%ChangeOI"] = ((df["PE_OI"].diff().fillna(0) / df["PE_OI"].shift(1).fillna(df["PE_OI"])) * 100)

# Round to 1 decimal place
df["CE_%ChangeOI"] = df["CE_%ChangeOI"].round(1).apply(lambda x: f"{x:+.1f}")
df["PE_%ChangeOI"] = df["PE_%ChangeOI"].round(1).apply(lambda x: f"{x:+.1f}")
df["CE_LTP"] = df["CE_LTP"].round(1)
df["PE_LTP"] = df["PE_LTP"].round(1)
df["strikePrice"] = df["strikePrice"].round(1)

# ----------------- FILTER ±5 STRIKES AROUND ATM -----------------
atm_strike = min(df["strikePrice"], key=lambda x: abs(x - spot_price))
df_filtered = df[(df["strikePrice"] >= atm_strike - 5*100) &
                 (df["strikePrice"] <= atm_strike + 5*100)].copy()

# ----------------- PCR CALCULATION -----------------
total_pcr = df_filtered["PE_OI"].sum() / df_filtered["CE_OI"].sum()
trend = "🟢 Bullish" if total_pcr > 1 else "🔴 Bearish"
st.markdown(f"**PCR (Put/Call Ratio): {total_pcr:.2f} → {trend}**")

# ----------------- INTRINSIC VALUE FOR ATM -----------------
atm_idx = df_filtered.index[df_filtered["strikePrice"] == atm_strike][0]
atm_row = df_filtered.loc[atm_idx]

call_intrinsic = max(0, spot_price - atm_strike)
put_intrinsic = max(0, atm_strike - spot_price)

call_diff = round(call_intrinsic - atm_row["PE_LTP"], 1)
put_diff = round(put_intrinsic - atm_row["CE_LTP"], 1)

# Add columns for intrinsic comparison (ATM row only)
df_filtered["CE_Intrinsic_vs_PE"] = ""
df_filtered["PE_Intrinsic_vs_CE"] = ""

if call_diff > 0:
    df_filtered.at[atm_idx, "CE_Intrinsic_vs_PE"] = f"🟢 +₹{call_diff:.1f}"
if put_diff > 0:
    df_filtered.at[atm_idx, "PE_Intrinsic_vs_CE"] = f"🟢 +₹{put_diff:.1f}"

# ----------------- HIGHLIGHT HIGHEST OI -----------------
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

# ----------------- DISPLAY TABLE -----------------
st.write("### 🔍 Current Week Option Chain (±5 Strikes from ATM)")

styled_df = df_filtered[[
    "CE_OI", "CE_%ChangeOI", "CE_LTP", "CE_Intrinsic_vs_PE",
    "strikePrice",
    "PE_Intrinsic_vs_CE", "PE_LTP", "PE_%ChangeOI", "PE_OI"
]].style.apply(highlight_row, axis=1)

st.dataframe(styled_df, use_container_width=True, hide_index=True)

# ----------------- BAR CHARTS -----------------
st.write("### 📈 OI Distribution (CE vs PE % Change)")
col1, col2 = st.columns(2)
with col1:
    st.bar_chart(df_filtered.set_index("strikePrice")["CE_%ChangeOI"].astype(float))
with col2:
    st.bar_chart(df_filtered.set_index("strikePrice")["PE_%ChangeOI"].astype(float))

# ----------------- REFRESH BUTTON -----------------
if st.button("🔄 Refresh Data"):
    st.cache_data.clear()
    st.rerun()
