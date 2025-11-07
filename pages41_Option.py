#!/usr/bin/env python3
import requests
import pandas as pd
import streamlit as st
from datetime import datetime, timedelta, timezone
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
SMTP_USER = "trim15081947@gmail.com"
SMTP_PASS = "yvrpgfrzersyanvx"
ALERT_EMAIL = "trim15081947@gmail.com"

# ===========================
st.set_page_config(
    page_title="NIFTY & BANKNIFTY Option Chain — OI Tracker + Email Alerts",
    layout="wide"
)

# -----------------
# Auto-refresh (30 seconds)
# -----------------
_ = st_autorefresh(interval=30000, limit=None, key="refresh_counter")  # 30s

# -----------------
# Email helpers (STARTTLS)
# -----------------
def send_email_simple(subject: str, body_text: str, to_addr: str = ALERT_EMAIL):
    """Sends a plain-text email using STARTTLS (port 587)."""
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = to_addr
        msg.set_content(body_text)

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(SMTP_USER, SMTP_PASS)
            smtp.send_message(msg)
        return True, None
    except Exception as ex:
        return False, str(ex)


def send_email_html(subject: str, html_body: str, plain_text: str = None, to_addr: str = ALERT_EMAIL):
    """Sends an HTML email (multipart alternative)."""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = to_addr

        if plain_text is None:
            plain_text = "See HTML content."

        part1 = MIMEText(plain_text, "plain")
        part2 = MIMEText(html_body, "html")
        msg.attach(part1)
        msg.attach(part2)

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(SMTP_USER, SMTP_PASS)
            smtp.send_message(msg)
        return True, None
    except Exception as ex:
        return False, str(ex)
# -----------------
# Helpers for NSE option chain fetching
# -----------------
def fetch_option_chain(symbol: str):
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


# -----------------
# Time helper: IST timezone
# -----------------
IST = timezone(timedelta(hours=5, minutes=30))

def now_ist():
    return datetime.now(IST)

def fmt_ist(dt: datetime):
    """Format as DD-MMM-YYYY HH:MM:SS IST"""
    return dt.strftime("%d-%b-%Y %H:%M:%S") + " IST"
# -----------------
# Streamlit UI
# -----------------
st.title("📊 NIFTY & BANKNIFTY Option Chain Tracker with Flip Alerts")

# Sidebar inputs
st.sidebar.header("Settings")

symbol = st.sidebar.selectbox(
    "Select Symbol",
    ["NIFTY", "BANKNIFTY"],
    index=0
)

refresh_interval = st.sidebar.slider(
    "Auto-refresh interval (seconds)",
    15, 120, 30
)

email_alerts = st.sidebar.checkbox("Enable Email Alerts", value=True)

st.sidebar.write(f"📧 Alerts will be sent to: `{ALERT_EMAIL}`")

# -----------------
# Initialize session states
# -----------------
if "last_flip" not in st.session_state:
    st.session_state.last_flip = None
if "last_summary_sent" not in st.session_state:
    st.session_state.last_summary_sent = now_ist() - timedelta(minutes=10)
if "prev_sentiment" not in st.session_state:
    st.session_state.prev_sentiment = ""
if "flip_counter" not in st.session_state:
    st.session_state.flip_counter = 0
# -----------------
# Fetch option chain data
# -----------------
st.info(f"Fetching data for **{symbol}**...")

try:
    data = fetch_option_chain(symbol)
    records = data["records"]["data"]
except Exception as e:
    st.error(f"⚠️ Failed to fetch data from NSE: {e}")
    st.stop()

# -----------------
# Process data
# -----------------
rows = []
for r in records:
    strike = r.get("strikePrice", 0)
    ce = r.get("CE", {})
    pe = r.get("PE", {})

    ce_oi = safe_int(ce.get("openInterest", 0))
    pe_oi = safe_int(pe.get("openInterest", 0))
    ce_chg_oi = safe_int(ce.get("changeinOpenInterest", 0))
    pe_chg_oi = safe_int(pe.get("changeinOpenInterest", 0))
    ce_ltp = safe_int(ce.get("lastPrice", 0))
    pe_ltp = safe_int(pe.get("lastPrice", 0))

    rows.append({
        "Strike": strike,
        "CE_OI": ce_oi,
        "CE_Change_OI": ce_chg_oi,
        "CE_LTP": ce_ltp,
        "PE_OI": pe_oi,
        "PE_Change_OI": pe_chg_oi,
        "PE_LTP": pe_ltp
    })

df = pd.DataFrame(rows)
df = df[df["Strike"] > 0]
df.sort_values(by="Strike", inplace=True)
df.reset_index(drop=True, inplace=True)

# -----------------
# Compute OI differential and Sentiment
# -----------------
df["OI_Diff"] = ((df["PE_OI"] - df["CE_OI"]) / 1000).round(2)

total_ce_oi = df["CE_OI"].sum()
total_pe_oi = df["PE_OI"].sum()

if total_pe_oi > total_ce_oi * 1.1:
    sentiment = "🟢 Strong Bullish"
elif total_ce_oi > total_pe_oi * 1.1:
    sentiment = "🔴 Strong Bearish"
elif total_pe_oi > total_ce_oi:
    sentiment = "🟡 Bullish but Risky"
else:
    sentiment = "🟠 Bearish but Risky"

st.subheader(f"Market Sentiment → {sentiment}")

# -----------------
# Detect flips
# -----------------
flip_triggered = False
if st.session_state.prev_sentiment and sentiment != st.session_state.prev_sentiment:
    flip_triggered = True
    st.session_state.flip_counter += 1
    st.session_state.last_flip = now_ist()
st.session_state.prev_sentiment = sentiment
# -----------------
# Display main data table
# -----------------
st.dataframe(
    df[["Strike", "CE_OI", "PE_OI", "OI_Diff", "CE_Change_OI", "PE_Change_OI", "CE_LTP", "PE_LTP"]],
    use_container_width=True
)

# -----------------
# Flip notification and email
# -----------------
if flip_triggered:
    flip_time = fmt_ist(st.session_state.last_flip)
    subject = f"🔄 {symbol} Option Sentiment Flip — {sentiment}"
    body = f"""
    Flip detected at {flip_time}\n
    Symbol: {symbol}\n
    Sentiment changed to: {sentiment}\n
    Total CE OI: {total_ce_oi:,}\n
    Total PE OI: {total_pe_oi:,}\n
    Flip Count: {st.session_state.flip_counter}
    """
    st.warning(body)

    if email_alerts:
        ok, err = send_email_simple(subject, body)
        if ok:
            st.success("✅ Email alert sent!")
        else:
            st.error(f"⚠️ Email sending failed: {err}")

# -----------------
# Periodic summary (every 10 minutes)
# -----------------
now = now_ist()
if (now - st.session_state.last_summary_sent) >= timedelta(minutes=10):
    summary_subject = f"📈 {symbol} Summary Update — {fmt_ist(now)}"
    summary_html = f"""
    <h3>{symbol} Option Chain Summary</h3>
    <p><b>Sentiment:</b> {sentiment}</p>
    <p><b>Total CE OI:</b> {total_ce_oi:,}</p>
    <p><b>Total PE OI:</b> {total_pe_oi:,}</p>
    <p><b>Last Flip:</b> {fmt_ist(st.session_state.last_flip) if st.session_state.last_flip else 'N/A'}</p>
    <p><b>Flip Count:</b> {st.session_state.flip_counter}</p>
    """
    if email_alerts:
        ok, err = send_email_html(summary_subject, summary_html)
        if ok:
            st.info("📤 Summary email sent.")
        else:
            st.error(f"Summary email failed: {err}")
    st.session_state.last_summary_sent = now
