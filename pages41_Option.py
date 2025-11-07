#!/usr/bin/env python3
import requests
import pandas as pd
import streamlit as st
from datetime import datetime, timedelta, timezone
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from streamlit_autorefresh import st_autorefresh
import plotly.express as px

# ============================================================
# = SMTP CONFIGURATION =
# ============================================================
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = "trim15081947@gmail.com"
SMTP_PASS = "yvrpgfrzersyanvx"
SEND_TO = "trim15081947@gmail.com"

# ============================================================
# = STREAMLIT PAGE CONFIG =
# ============================================================
st.set_page_config(page_title="Option Chain Tracker", layout="wide")
st.title("📊 NIFTY Option Chain Tracker")

# Auto-refresh every 60 seconds
st_autorefresh(interval=60 * 1000, key="datarefresh")

# ============================================================
# = SESSION STATE INIT =
# ============================================================
if "last_summary_sent" not in st.session_state:
    st.session_state.last_summary_sent = datetime.now(timezone.utc)
if "last_flip_time" not in st.session_state:
    st.session_state.last_flip_time = "N/A"
if "alert_cooldown" not in st.session_state:
    st.session_state.alert_cooldown = {}

# ============================================================
# = FETCH NSE OPTION CHAIN =
# ============================================================
def fetch_option_chain(symbol="NIFTY"):
    url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        rows = []
        for item in data["records"]["data"]:
            strike = item["strikePrice"]
            ce = item.get("CE", {})
            pe = item.get("PE", {})
            rows.append({
                "StrikePrice": strike,
                "CE_OI": ce.get("openInterest", 0),
                "CE_%OI": ce.get("changeinOpenInterest", 0),
                "PE_OI": pe.get("openInterest", 0),
                "PE_%OI": pe.get("changeinOpenInterest", 0)
            })
        return pd.DataFrame(rows)
    except Exception as e:
        st.error(f"NSE fetch error: {e}")
        return pd.DataFrame()

# ============================================================
# = EMAIL FUNCTION =
# ============================================================
def send_email(subject, body):
    try:
        msg = MIMEMultipart()
        msg["From"] = SMTP_USER
        msg["To"] = ", ".join(SEND_TO)
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        st.info(f"📧 Email Sent: {subject}")
    except Exception as e:
        st.warning(f"Email error: {e}")

# ============================================================
# = HELPER: COOLDOWN CHECK =
# ============================================================
def can_send_alert(alert_name, cooldown_minutes=3):
    now = datetime.now(timezone.utc)
    last_sent = st.session_state.alert_cooldown.get(alert_name)
    if not last_sent or (now - last_sent) > timedelta(minutes=cooldown_minutes):
        st.session_state.alert_cooldown[alert_name] = now
        return True
    return False

# ============================================================
# = MAIN LOGIC =
# ============================================================
df = fetch_option_chain("NIFTY")

if df.empty:
    st.warning("⚠️ NSE Data Unavailable.")
else:
    # Compute OI_Diff
    df["OI_Diff"] = (
        (df["PE_OI"] * (df["PE_%OI"] / (100 + df["PE_%OI"])))
        - (df["CE_OI"] * (df["CE_%OI"] / (100 + df["CE_%OI"])))
    ) / 1000

    total_ce_oi = df["CE_OI"].sum()
    total_pe_oi = df["PE_OI"].sum()

    # Risk metric
    diff_ratio = abs(total_ce_oi - total_pe_oi) / max(total_ce_oi, total_pe_oi)
    risk_label = (
        "Low" if diff_ratio > 0.25 else
        "Moderate" if diff_ratio > 0.10 else
        "High"
    )

    # Sentiment
    if total_pe_oi > total_ce_oi * 1.2:
        sentiment = "Strong Bullish"
    elif total_ce_oi > total_pe_oi * 1.2:
        sentiment = "Strong Bearish"
    elif total_pe_oi > total_ce_oi:
        sentiment = "Bullish but Risky"
    else:
        sentiment = "Bearish but Risky"

    # ============================================================
    # = SIGNAL SUMMARY DASHBOARD =
    # ============================================================
    st.markdown("### 🧾 Market Summary")
    st.markdown(
        f"""
        <div style="border-radius:10px;padding:12px;background-color:#1e1e1e;color:white;">
        <b>Market Sentiment:</b> {sentiment}<br>
        <b>CE OI (Calls):</b> {total_ce_oi/1e5:.2f} Cr &nbsp;|&nbsp;
        <b>PE OI (Puts):</b> {total_pe_oi/1e5:.2f} Cr<br>
        <b>Risk Level:</b> {risk_label} &nbsp;|&nbsp;
        <b>Last Flip:</b> {st.session_state.last_flip_time}
        </div>
        """,
        unsafe_allow_html=True
    )

    # ============================================================
    # = DISPLAY TABLE =
    # ============================================================
    st.dataframe(
        df[["StrikePrice", "CE_OI", "CE_%OI", "PE_OI", "PE_%OI", "OI_Diff"]]
        .sort_values("StrikePrice"),
        use_container_width=True, height=480
    )

    # ============================================================
    # = OI DIFF CHART (PLOTLY) =
    # ============================================================
    df_sorted = df.sort_values("StrikePrice").copy()
    df_sorted["Trend"] = ["Bullish" if x > 0 else "Bearish" for x in df_sorted["OI_Diff"]]

    fig = px.bar(
        df_sorted,
        x="StrikePrice",
        y="OI_Diff",
        color="Trend",
        color_discrete_map={"Bullish": "green", "Bearish": "red"},
        title="OI Difference (PE-CE) per Strike"
    )
    fig.update_layout(template="plotly_dark", height=400, showlegend=False)
    st.plotly_chart(fig, use_container_width=True)

    # ============================================================
    # = ALERT LOGIC =
    # ============================================================
    flip_detected = False
    if "last_signal" not in st.session_state:
        st.session_state.last_signal = sentiment

    if st.session_state.last_signal != sentiment:
        flip_detected = True
        st.session_state.last_signal = sentiment
        st.session_state.last_flip_time = datetime.now().strftime("%H:%M:%S")

    # Send Flip Alert
    if flip_detected and can_send_alert("sentiment_flip"):
        send_email(
            f"⚡ Flip Alert: {sentiment}",
            f"<b>New Signal:</b> {sentiment}<br><b>Time:</b> {st.session_state.last_flip_time}"
        )

    # OI Diff Flip Alert
    if can_send_alert("oidiff_flip") and abs(df["OI_Diff"].sum()) > 500:
        trend = "Bullish" if df["OI_Diff"].sum() > 0 else "Bearish"
        send_email(
            f"OI Diff Flip Alert: {trend}",
            f"Total OI_Diff: {df['OI_Diff'].sum():,.0f}k<br>Trend: {trend}"
        )

    # CE Risk Flip Alert
    if can_send_alert("ce_risk_flip") and total_ce_oi > total_pe_oi * 1.3:
        send_email("⚠️ CE Risk Flip", f"CE OI ({total_ce_oi}) exceeds PE OI ({total_pe_oi}) significantly.")

    # PE Risk Flip Alert
    if can_send_alert("pe_risk_flip") and total_pe_oi > total_ce_oi * 1.3:
        send_email("⚠️ PE Risk Flip", f"PE OI ({total_pe_oi}) exceeds CE OI ({total_ce_oi}) significantly.")

    # CE–PE Diff Flip Alert
    if can_send_alert("cepe_diff_flip") and abs(total_pe_oi - total_ce_oi) > 1e6:
        send_email("🔁 CE-PE OI Difference Alert", f"OI difference = {abs(total_pe_oi - total_ce_oi):,.0f}")

    # ============================================================
    # = PERIODIC SUMMARY (EVERY 60s) =
    # ============================================================
    now = datetime.now(timezone.utc)
    if (now - st.session_state.last_summary_sent) > timedelta(seconds=60):
        st.session_state.last_summary_sent = now
        summary_html = f"""
        <h4>📊 60-Second Summary</h4>
        <b>Market Sentiment:</b> {sentiment}<br>
        <b>CE OI:</b> {total_ce_oi/1e5:.2f} Cr &nbsp;|&nbsp;
        <b>PE OI:</b> {total_pe_oi/1e5:.2f} Cr<br>
        <b>Risk:</b> {risk_label}<br>
        <b>Time:</b> {datetime.now().strftime('%H:%M:%S')}
        """
        send_email("📈 NIFTY 60s Summary", summary_html)

# ============================================================
# = END OF FILE =
# ============================================================
