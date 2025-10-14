import streamlit as st
import requests
import pandas as pd
import time

st.set_page_config(page_title="NIFTY OI Monitor", page_icon="📈", layout="centered")

def fetch_oi():
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "en-US,en;q=0.9",
    }
    url = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
    session = requests.Session()
    session.get("https://www.nseindia.com", headers=headers)
    response = session.get(url, headers=headers)
    data = response.json()
    records = data["records"]["data"]
    underlying = data["records"]["underlyingValue"]
    expiry = data["records"]["expiryDates"][0]
    rows = []
    for d in records:
        if d.get("expiryDate") == expiry:
            strike = d["strikePrice"]
            ce = d.get("CE", {}).get("changeinOpenInterest", None)
            pe = d.get("PE", {}).get("changeinOpenInterest", None)
            rows.append([strike, ce, pe])
    df = pd.DataFrame(rows, columns=["Strike", "Call Chg OI", "Put Chg OI"])
    df = df.sort_values("Strike")
    atm = df.iloc[(df["Strike"] - underlying).abs().argsort()[:1]]["Strike"].values[0]
    atm_index = df.index[df["Strike"] == atm][0]
    subset = df.iloc[max(atm_index - 5, 0): atm_index + 6]
    return underlying, expiry, atm, subset

st.title("📊 Live NIFTY Open Interest Monitor")

refresh_time = 60
placeholder = st.empty()

while True:
    try:
        underlying, expiry, atm, df = fetch_oi()
        with placeholder.container():
            st.subheader(f"NIFTY: {underlying:.2f}")
            st.caption(f"Expiry: {expiry} | ATM Strike: {atm}")
            st.dataframe(df, hide_index=True, use_container_width=True)
            st.info(f"Auto-refreshes every {refresh_time} seconds")
        time.sleep(refresh_time)
    except Exception as e:
        st.error(f"Error fetching data: {e}")
        time.sleep(10)
