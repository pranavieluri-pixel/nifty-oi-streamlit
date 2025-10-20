# pages11_Option_with_email.py
import requests
import pandas as pd
import streamlit as st
from datetime import datetime
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="NIFTY & BANKNIFTY Option Chain - OI Tracker", layout="wide")

# ----------------- Auto-refresh (30 seconds) -----------------
_ = st_autorefresh(interval=30000, limit=None, key="refresh_counter")  # 30s

# ----------------- Email helpers -----------------
def send_gmail(subject: str, body: str, sender: str, recipient: str, gmail_user: str, gmail_pass: str, port: int = 465):
    """Send an email via Gmail SMTP (SSL port 465 by default)."""
    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", port, context=context) as server:
        server.login(gmail_user, gmail_pass)
        server.send_message(msg)

def format_atm_row_email(index_name, expiry, atm_row: pd.Series, spot, prev_sign, curr_sign, time_str):
    s = []
    s.append("ATM Sign Change Alert")
    s.append("")
    s.append(f"Index: {index_name}")
    s.append(f"Expiry: {expiry}")
    s.append(f"ATM Strike: {atm_row.get('StrikeLabel','')}")
    s.append("")
    s.append("FULL ATM ROW (display columns):")
    keys = ["CE_OI","CE_%OI","CE_Risk","CE_PE_Diff","CE_LTP","StrikeLabel","SPOT","PE_LTP","PE_Risk","PE_%OI","PE_OI"]
    for k in keys:
        val = atm_row.get(k, "")
        s.append(f"{k}: {val}")
    s.append("")
    ce_risk = atm_row.get("CE_Risk", 0)
    pe_risk = atm_row.get("PE_Risk", 0)
    diff = atm_row.get("CE_PE_Diff", 0)
    direction = "BULLISH" if diff > 0 else ("BEARISH" if diff < 0 else "NEUTRAL")
    flip_text = f"{prev_sign} → {curr_sign}" if prev_sign is not None else f"{curr_sign} (initial)"
    s.append(f"Status: {direction} ({flip_text})")
    s.append("")
    s.append(f"CE_Risk = {ce_risk}")
    s.append(f"PE_Risk = {pe_risk}")
    s.append(f"Diff = {diff} ({'Positive→Call side stronger' if diff>0 else ('Negative→Put side stronger' if diff<0 else 'Neutral')})")
    s.append("")
    s.append(f"Time: {time_str}")
    return "\n".join(s)

# ----------------- Helpers -----------------
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

# ----------------- UI -----------------
st.title("📊 NIFTY / BANKNIFTY Option Chain — OI Tracker + Email Alerts")
symbol = st.radio("Select Index", ["NIFTY", "BANKNIFTY"], horizontal=True)

# Manual refresh (label chosen earlier)
if st.button("♻️ Manual Refresh"):
    st.rerun()

# ----------------- Fetch data -----------------
try:
    raw = fetch_option_chain(symbol)
except Exception as e:
    st.error(f"Failed to fetch option chain: {e}")
    st.stop()

records = raw.get("records") or {}
expiry_dates = records.get("expiryDates") or []
data_list = records.get("data") or raw.get("filtered", {}).get("data") or raw.get("data") or []

# robust underlying value lookup
underlying_value = records.get("underlyingValue") or raw.get("underlyingValue") or None
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
selected_expiry = st.selectbox("Select Expiry (default = current week)", options=expiry_dates, index=0)
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

    ce_ltp = safe_int(ce.get("lastPrice", 0))
    pe_ltp = safe_int(pe.get("lastPrice", 0))

    # risk formulas as per your chosen approach
    ce_risk = safe_int(ce_ltp - ce_iv)  # CE_LTP - CE_IV
    pe_risk = safe_int(pe_ltp - pe_iv)  # PE_LTP - PE_IV

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
if df.empty:
    st.error("No strike data available.")
    st.stop()

# ----------------- ATM-centric selection (±5 strikes) -----------------
atm_idx_full = (df["strikePrice"] - spot_price).abs().idxmin()
window_before = 5
window_after = 5
start_idx = max(0, int(atm_idx_full) - window_before)
end_idx = min(len(df) - 1, int(atm_idx_full) + window_after)
df_filtered = df.iloc[start_idx:end_idx + 1].copy().reset_index(drop=True)
df_filtered = df_filtered.sort_values("strikePrice").reset_index(drop=True)

atm_idx_filtered = (df_filtered["strikePrice"] - spot_price).abs().idxmin()
atm_strike = int(df_filtered.loc[atm_idx_filtered, "strikePrice"])

# Derived
df_filtered["CE_PE_Diff"] = df_filtered["CE_Risk"] - df_filtered["PE_Risk"]

# PCRs
total_pe_oi = int(df_filtered["PE_OI"].sum())
total_ce_oi = int(df_filtered["CE_OI"].sum())
total_pcr = (total_pe_oi / total_ce_oi) if total_ce_oi != 0 else float("inf")
trend = "🟢 Bullish" if total_pcr > 1 else "🔴 Bearish"

start_atm_idx = max(0, int(atm_idx_filtered) - 4)
end_atm_idx = min(len(df_filtered) - 1, int(atm_idx_filtered) + 4)
df_atm_window = df_filtered.iloc[start_atm_idx:end_atm_idx+1]
atm_pe_oi = int(df_atm_window["PE_OI"].sum())
atm_ce_oi = int(df_atm_window["CE_OI"].sum())
atm_pcr = (atm_pe_oi / atm_ce_oi) if atm_ce_oi != 0 else float("inf")
atm_trend = "🟢 Bullish" if atm_pcr > 1 else "🔴 Bearish"

atm_row = df_filtered.iloc[atm_idx_filtered]

# ----------------- Prepare display DataFrame -----------------
display = df_filtered.copy()
display["StrikeLabel"] = display["strikePrice"].apply(lambda s: f"[ATM] {int(s)}" if int(s) == atm_strike else f"{int(s)}")
display["SPOT"] = safe_int(spot_price)
display = display.rename(columns={"CE_pchgOI": "CE_%OI", "PE_pchgOI": "PE_%OI"})
display = display[[
    "CE_OI","CE_%OI","CE_Risk","CE_PE_Diff","CE_LTP","StrikeLabel","SPOT","PE_LTP","PE_Risk","PE_%OI","PE_OI"
]]

# ensure integer columns (if present)
for c in ["CE_OI","CE_%OI","CE_Risk","CE_PE_Diff","CE_LTP","SPOT","PE_LTP","PE_Risk","PE_%OI","PE_OI"]:
    if c in display.columns:
        display[c] = display[c].fillna(0).astype(int)

# ----------------- Session state for sign flip detection -----------------
if "prev_ce_pe_diff_sign" not in st.session_state:
    st.session_state.prev_ce_pe_diff_sign = None
    st.session_state.prev_ce_pe_diff_value = None

# compute current sign
curr_diff_val = int(display.loc[atm_idx_filtered, "CE_PE_Diff"])
if curr_diff_val > 0:
    curr_sign = "Positive"
elif curr_diff_val < 0:
    curr_sign = "Negative"
else:
    curr_sign = "Zero"

prev_sign = st.session_state.get("prev_ce_pe_diff_sign", None)
should_trigger = False
if prev_sign is not None:
    if (prev_sign == "Positive" and curr_sign == "Negative") or (prev_sign == "Negative" and curr_sign == "Positive"):
        should_trigger = True

# update session state
st.session_state.prev_ce_pe_diff_sign = curr_sign
st.session_state.prev_ce_pe_diff_value = curr_diff_val

# ----------------- Rocket logic (unchanged) -----------------
atm_ce_pct = int(atm_row.get("CE_pchgOI", 0))
atm_pe_pct = int(atm_row.get("PE_pchgOI", 0))
rocket_symbol = "⚪"
rocket_text = "Neutral"
if (total_pcr > 1) and (atm_pe_oi > atm_ce_oi) and (atm_pe_pct > 0):
    rocket_symbol = "🟢🚀"; rocket_text = "Strong Bullish"
elif (total_pcr < 1) and (atm_ce_oi > atm_pe_oi) and (atm_ce_pct > 0):
    rocket_symbol = "🔴🚀"; rocket_text = "Strong Bearish"
else:
    if (total_pcr > 1 and atm_pe_oi > atm_ce_oi) or (atm_pe_pct > 0 and atm_pe_oi > atm_ce_oi):
        rocket_symbol = "🟡⚠️"; rocket_text = "Bullish but Risky"
    elif (total_pcr < 1 and atm_ce_oi > atm_pe_oi) or (atm_ce_pct > 0 and atm_ce_oi > atm_pe_oi):
        rocket_symbol = "🟡⚠️"; rocket_text = "Bearish but Risky"
    else:
        rocket_symbol = "🤔"; rocket_text = "Conflict / Wait"

# ----------------- Email trigger -----------------
if should_trigger:
    # prepare subject and body
    subject_direction = "Bullish → Bearish" if (prev_sign == "Positive" and curr_sign == "Negative") else "Bearish → Bullish"
    subject = f"{subject_direction} ({datetime.now().strftime('%H:%M:%S')})"
    atm_display_row = display.iloc[atm_idx_filtered]
    time_str = datetime.now().strftime("%H:%M:%S")
    body = format_atm_row_email(symbol, selected_expiry, atm_display_row, spot_price, prev_sign, curr_sign, time_str)

    # read secrets (must exist)
    secrets_ok = True
    try:
        gmail_user = st.secrets["GMAIL_USER"]
        gmail_pass = st.secrets["GMAIL_PASS"]
        alert_recipient = st.secrets["ALERT_EMAIL"]
    except Exception:
        secrets_ok = False
        st.error("Missing GMAIL_USER / GMAIL_PASS / ALERT_EMAIL in .streamlit/secrets.toml. Add them and retry.")

    if secrets_ok:
        sender_email = gmail_user
        receiver_email = alert_recipient
        try:
            send_gmail(subject, body, sender_email, receiver_email, gmail_user, gmail_pass, port=465)
            st.success(f"Email alert sent to {receiver_email}: {subject}")
        except Exception as ex:
            st.error(f"Failed to send alert email: {ex}")

# ----------------- Styling & display -----------------
st.markdown("---")
pcr_display = (f"{total_pcr:.2f}" if total_pcr != float("inf") else "∞")
atm_pcr_display = (f"{atm_pcr:.2f}" if atm_pcr != float("inf") else "∞")
st.markdown(
    f"**Live Snapshot:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
    f"Spot: {safe_int(spot_price)} | "
    f"PCR (all shown): {pcr_display} → {trend} | "
    f"PCR (ATM ±4): {atm_pcr_display} → {atm_trend} | {rocket_symbol} {rocket_text}"
)

st.write("### 🔍 ATM ±5 Strike Option Chain (ascending strikes)")
st.dataframe(display, use_container_width=True, hide_index=True)
