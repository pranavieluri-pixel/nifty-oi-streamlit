#!/usr/bin/env python3
import requests
import pandas as pd
import streamlit as st
from datetime import datetime, timedelta
import smtplib
from email.message import EmailMessage
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from streamlit_autorefresh import st_autorefresh

# ===========================
# = User SMTP Credentials =
# ===========================
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_EMAIL = "your_email@gmail.com"
SMTP_PASS = "your_app_password"
ALERT_EMAIL = "alert_recipient@gmail.com"

# ===========================
# = Streamlit Page Setup =
# ===========================
st.set_page_config(page_title="Option Chain Tracker", layout="wide")
st.title("📊 Option Chain Tracker (with OI_Diff Alert)")

# ===========================
# = Auto Refresh Logic =
# ===========================
st_autorefresh(interval=60 * 1000, key="refresh")

if "last_summary_sent" not in st.session_state:
    st.session_state.last_summary_sent = datetime.now() - timedelta(seconds=61)

# ===========================
# = Fetch Option Chain Data =
# ===========================
def fetch_option_chain():
    url = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        records = []
        for i in data["records"]["data"]:
            if "CE" in i and "PE" in i:
                records.append({
                    "StrikeLabel": i["strikePrice"],
                    "CE_OI": i["CE"]["openInterest"],
                    "CE_%OI": i["CE"]["changeinOpenInterest"],
                    "CE_LTP": i["CE"]["lastPrice"],
                    "PE_OI": i["PE"]["openInterest"],
                    "PE_%OI": i["PE"]["changeinOpenInterest"],
                    "PE_LTP": i["PE"]["lastPrice"],
                })
        return pd.DataFrame(records)
    except Exception as e:
        st.error(f"Failed to fetch data: {e}")
        return pd.DataFrame()

display = fetch_option_chain()

if not display.empty:
    # =====================================================
    # = OI_Diff Column + Sign Flip Email Alert Integration =
    # =====================================================
    display["OI_Diff"] = (
        (display["PE_OI"] * (1 + display["PE_%OI"] / 100) -
         display["CE_OI"] * (1 + display["CE_%OI"] / 100)) / 100
    ).astype(int)

    # Make OI_Diff first column
    cols = ["OI_Diff"] + [c for c in display.columns if c != "OI_Diff"]
    display = display[cols]

    # Color function for OI_Diff
    def color_oi_diff(val):
        color = "green" if val > 0 else "red"
        return f"color:{color}; font-weight:600;"

    # ---- Sign flip latch alert ----
    current_sign = 1 if display["OI_Diff"].sum() > 0 else -1
    if "last_oi_sign" not in st.session_state:
        st.session_state.last_oi_sign = current_sign
    else:
        if current_sign != st.session_state.last_oi_sign:
            st.session_state.last_oi_sign = current_sign
            direction = "Bullish (PE > CE)" if current_sign > 0 else "Bearish (CE > PE)"
            msg = f"⚠️ OI_Diff Sign Flip Detected!\n\nNew Bias: {direction}\nTimestamp: {datetime.now().strftime('%H:%M:%S')}"

            try:
                email = EmailMessage()
                email["From"] = SMTP_EMAIL
                email["To"] = ALERT_EMAIL
                email["Subject"] = f"Option Alert: OI_Diff Flip → {direction}"
                email.set_content(msg)

                with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
                    smtp.starttls()
                    smtp.login(SMTP_EMAIL, SMTP_PASS)
                    smtp.send_message(email)

                st.success(f"📩 Email alert sent for OI_Diff Flip → {direction}")
            except Exception as e:
                st.error(f"Email alert failed: {e}")

    # ===========================
    # = Display in Streamlit =
    # ===========================
    st.dataframe(display.style.applymap(color_oi_diff, subset=["OI_Diff"]))

    # ====================================================
    # = Periodic Summary Email Every 60 Seconds (Enhanced) =
    # ====================================================
    now = datetime.now()
    SUMMARY_INTERVAL = timedelta(seconds=60)

    if (now - st.session_state.last_summary_sent) >= SUMMARY_INTERVAL:
        try:
            max_put_strike = display.loc[display["PE_OI"].idxmax(), "StrikeLabel"]
            max_call_strike = display.loc[display["CE_OI"].idxmax(), "StrikeLabel"]

            # OI_Diff Summary for Email
            latest_oi_diff = int(display["OI_Diff"].sum())
            oi_direction = "Bullish (PE > CE)" if latest_oi_diff > 0 else "Bearish (CE > PE)"
            oi_color = "green" if latest_oi_diff > 0 else "red"

            # Build Email Body
            html_content = f"""
            <html>
            <body>
                <h3>📊 NIFTY Option Summary — {datetime.now().strftime('%H:%M:%S')}</h3>
                <p><b>Max PUT OI Strike:</b> {max_put_strike}</p>
                <p><b>Max CALL OI Strike:</b> {max_call_strike}</p>
                <p><b>OI_Diff:</b> <span style='color:{oi_color}'>{latest_oi_diff}</span> — {oi_direction}</p>
                <p><i>Auto-generated alert summary from Option Tracker.</i></p>
            </body>
            </html>
            """

            msg = MIMEMultipart("alternative")
            msg["Subject"] = "Option Summary Update"
            msg["From"] = SMTP_EMAIL
            msg["To"] = ALERT_EMAIL
            msg.attach(MIMEText(html_content, "html"))

            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
                smtp.starttls()
                smtp.login(SMTP_EMAIL, SMTP_PASS)
                smtp.send_message(msg)

            st.session_state.last_summary_sent = now
            st.success("📩 Summary email with OI_Diff sent.")
        except Exception as e:
            st.error(f"Summary email failed: {e}")

else:
    st.warning("⚠️ No option chain data available.")
