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

st.set_page_config(page_title="NIFTY & BANKNIFTY Option Chain — OI Tracker + Email Alerts", layout="wide")

# ----------------- Auto-refresh (30 seconds) -----------------
_ = st_autorefresh(interval=30000, limit=None, key="refresh_counter")  # 30s

# ----------------- Email helpers (STARTTLS) -----------------
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

# ----------------- Helpers for NSE option chain fetching -----------------
def fetch_option_chain(symbol: str):
    url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/"
    }
    s = requests.Session()
    # initial GET to set cookies
    s.get("https://www.nseindia.com", headers=headers, timeout=5)
    r = s.get(url, headers=headers, timeout=10)
    r.raise_for_status()
    return r.json()

def safe_int(x):
    try:
        return int(round(float(x)))
    except Exception:
        return 0

# ----------------- Time helper: IST timezone -----------------
IST = timezone(timedelta(hours=5, minutes=30))
def now_ist():
    return datetime.now(IST)

def fmt_ist(dt: datetime):
    # format: DD-MMM-YYYY HH:MM:SS IST
    return dt.strftime("%d-%b-%Y %H:%M:%S") + " IST"

# ----------------- UI -----------------
st.title("📊 NIFTY / BANKNIFTY Option Chain — OI Tracker + Email Alerts (Flip + Summary)")

symbol = st.radio("Select Index", ["NIFTY", "BANKNIFTY"], horizontal=True)

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

    ce_risk = safe_int(ce_ltp - ce_iv)
    pe_risk = safe_int(pe_ltp - pe_iv)

    rows.append({
        "strikePrice": strike,
        "CE_OI": safe_int(ce.get("openInterest", 0)),
        "CE_%OI": safe_int(ce.get("pchangeinOpenInterest", 0)),
        "CE_LTP": ce_ltp,
        "CE_Risk": ce_risk,
        "PE_LTP": pe_ltp,
        "PE_%OI": safe_int(pe.get("pchangeinOpenInterest", 0)),
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
trend = "🟢 Bullish" if total_pcr > 0.85 else "🔴 Bearish"

start_atm_idx = max(0, int(atm_idx_filtered) - 4)
end_atm_idx = min(len(df_filtered) - 1, int(atm_idx_filtered) + 4)
df_atm_window = df_filtered.iloc[start_atm_idx:end_atm_idx+1]
atm_pe_oi = int(df_atm_window["PE_OI"].sum())
atm_ce_oi = int(df_atm_window["CE_OI"].sum())
atm_pcr = (atm_pe_oi / atm_ce_oi) if atm_ce_oi != 0 else float("inf")
atm_trend = "🟢 Bullish" if atm_pcr > 0.85 else "🔴 Bearish"
# ----------------- Prepare display DataFrame & OI_Diff -----------------
display = df_filtered.copy()
display["StrikeLabel"] = display["strikePrice"].apply(lambda s: f"[ATM] {int(s)}" if int(s) == atm_strike else f"{int(s)}")
display["SPOT"] = safe_int(spot_price)


# compute OI_Diff (float and rounded)
display["OI_Diff_Float"] = (
    (
        (display["PE_OI"] * (display["PE_%OI"] / (100 + display["PE_%OI"]))) -
        (display["CE_OI"] * (display["CE_%OI"] / (100 + display["CE_%OI"])))
    ) / 1000
).round(2)
display["OI_Diff"] = display["OI_Diff_Float"].round(0).astype(int)

# directional label for OI_Diff
def oi_direction_label(v):
    if v > 0:
        return "Bullish (PE > CE)"
    elif v < 0:
        return "Bearish (CE > PE)"
    else:
        return "Neutral"
display["OI_Diff_Dir"] = display["OI_Diff"].apply(oi_direction_label)



atm_row = df_filtered.iloc[atm_idx_filtered]

# ----------------- Prepare display DataFrame -----------------
display = df_filtered.copy()
display["StrikeLabel"] = display["strikePrice"].apply(lambda s: f"[ATM] {int(s)}" if int(s) == atm_strike else f"{int(s)}")
display["SPOT"] = safe_int(spot_price)
# ensure rename consistent
display = display.rename(columns={"CE_%OI": "CE_%OI", "PE_%OI": "PE_%OI"})

# ------------------------
# NEW: OI_Diff column (user requested)
# OI_Diff = ((PE_OI * (PE_%OI / (100+PE_%OI) ))) - ((CE_OI * (CE_%OI/ (100+CE_%OI)))) ) /1000
# placed as the first column, colored green if positive else red,
# and has a sign-change latch + email alert (separate from CE_PE_Diff latch).
# ------------------------
# guard missing columns
for col in ("PE_OI","CE_OI","PE_%OI","CE_%OI"):
    if col not in display.columns:
        display[col] = 0

# compute as float then round to int for display (keeps parity with other int columns)
display["OI_Diff"] = (
    (
        (display["PE_OI"] * (display["PE_%OI"] / (100 + display["PE_%OI"]))) -
        (display["CE_OI"] * (display["CE_%OI"] / (100 + display["CE_%OI"])))
    ) / 1000
)

# keep more precise numeric value for emails/logs (not just rounded)
display["OI_Diff_Float"] = display["OI_Diff"].round(2)
display["OI_Diff"] = display["OI_Diff"].round(0).astype(int)

# reorder so OI_Diff is first, then the rest (keep original column ordering you used)
ordered_cols = [
    "OI_Diff","CE_PE_Diff","StrikeLabel",
    "CE_%OI","PE_%OI","CE_Risk","CE_LTP","SPOT","PE_LTP","CE_OI","PE_Risk","PE_OI"
]
ordered_cols = [c for c in ordered_cols if c in display.columns]
display = display[ordered_cols].copy()

# Convert numeric display columns to int where appropriate (round OI_Diff first)
if "OI_Diff" in display.columns:
    display["OI_Diff"] = display["OI_Diff"].round().astype(int)

for c in ["CE_OI","CE_%OI","CE_Risk","CE_PE_Diff","CE_LTP","SPOT","PE_LTP","PE_Risk","PE_%OI","PE_OI"]:
    if c in display.columns:
        display[c] = display[c].fillna(0).astype(int)

# ----------------- Persisted state: history & last-sent markers -----------------
if "flip_history" not in st.session_state:
    # dict: strike -> list of {"time": datetime, "risk": int}
    st.session_state.flip_history = {}

if "last_flip_time" not in st.session_state:
    # dict: strike -> datetime when last flip email was sent (to avoid duplicates)
    st.session_state.last_flip_time = {}

if "last_summary_sent" not in st.session_state:
    st.session_state.last_summary_sent = datetime.min.replace(tzinfo=IST)

# NEW: state for OI_Diff sign latch & cooldown
if "oi_last_sign" not in st.session_state:
    # strikeLabel -> "Positive"/"Negative"/"Zero"
    st.session_state.oi_last_sign = {}

if "oi_last_oi_latch_time" not in st.session_state:
    # strikeLabel -> datetime last sent
    st.session_state.oi_last_oi_latch_time = {}

# NEW: state for CE and PE risk sign latches
if "ce_last_sign" not in st.session_state:
    st.session_state.ce_last_sign = {}
if "pe_last_sign" not in st.session_state:
    st.session_state.pe_last_sign = {}
if "ce_last_latch_time" not in st.session_state:
    st.session_state.ce_last_latch_time = {}
if "pe_last_latch_time" not in st.session_state:
    st.session_state.pe_last_latch_time = {}

# NEW: for storing last full snapshot to detect any change (for summary on every change)
if "last_snapshot" not in st.session_state:
    # map strikeLabel -> tuple(CE_OI, CE_%OI, CE_LTP, CE_Risk, PE_OI, PE_%OI, PE_LTP, PE_Risk, OI_Diff_Float)
    st.session_state.last_snapshot = {}

# NEW: OI Diff flip log (display in UI)
if "oi_flip_log" not in st.session_state:
    st.session_state.oi_flip_log = []

# ----------------- LATCH-BASED Flip detection + visual latch persistence -----------------
now = now_ist()

# initialize latch storage if missing
if "last_sign_state" not in st.session_state:
    st.session_state.last_sign_state = {}  # strike -> "Positive"/"Negative"/"Zero"

if "latched_strikes" not in st.session_state:
    # strike_label -> {"state": "BULLISH"/"BEARISH", "time": "HH:MM:SS"}
    st.session_state.latched_strikes = {}

flip_alerts = []   # list of (strike, prev_sign, curr_sign, prev_risk, curr_risk)
flip_symbols = []  # aligned with display rows

# Determine sign helper
def sign_of(x):
    try:
        xv = float(x)
    except Exception:
        xv = 0.0
    if xv > 0:
        return "Positive"
    elif xv < 0:
        return "Negative"
    else:
        return "Zero"

# Iterate rows, detect latch flips (this is the existing CE_PE_Diff latch)
for _, row in display.iterrows():
    strike_label = row["StrikeLabel"]
    # convert strike_label to plain numeric strike for logging/email
    try:
        strike_num = int(str(strike_label).replace("[ATM]", "").strip())
    except Exception:
        strike_num = None

    curr_risk = int(row["CE_PE_Diff"]) if "CE_PE_Diff" in row else 0
    curr_sign = sign_of(curr_risk)
    prev_sign = st.session_state.last_sign_state.get(str(strike_label))

    # If sign changed between Positive <-> Negative => it's a flip (ignore Zero intermediate flips)
    if prev_sign and prev_sign != "Zero" and curr_sign != "Zero" and prev_sign != curr_sign:
        # map sign to direction per user: CE>PE => Bullish (Positive), CE<PE => Bearish (Negative)
        curr_dir = "BULLISH" if curr_sign == "Positive" else "BEARISH"
        prev_dir = "BULLISH" if prev_sign == "Positive" else "BEARISH"

        # update latch storage to current direction and timestamp
        st.session_state.latched_strikes[str(strike_label)] = {
            "state": curr_dir,
            "time": now.strftime("%H:%M:%S")
        }

        # prevent sending too many emails for same strike in short time
        last_sent = st.session_state.last_flip_time.get(str(strike_label))
        allow_send = True
        COOLDOWN = timedelta(seconds=60)  # basic cooldown to avoid email spam
        if last_sent and (now - last_sent) < COOLDOWN:
            allow_send = False

        # prepare flip alert
        if allow_send:
            flip_alerts.append((strike_num, prev_sign, curr_sign, prev_sign, curr_risk))
            st.session_state.last_flip_time[str(strike_label)] = now

    # always update last_sign_state (latch remembers last seen sign)
    st.session_state.last_sign_state[str(strike_label)] = curr_sign

    # determine display Flip symbol using latched state (persist until changed)
    latched = st.session_state.latched_strikes.get(str(strike_label))
    symbol = "➖"
    if latched:
        if latched["state"] == "BULLISH":
            symbol = "✅"
        elif latched["state"] == "BEARISH":
            symbol = "🔴"
    flip_symbols.append(symbol)

# attach Flip column and reorder Flip right after StrikeLabel
display["Flip"] = flip_symbols
cols = display.columns.tolist()
if "Flip" in cols and "StrikeLabel" in cols:
    strike_idx = cols.index("StrikeLabel")
    cols.insert(strike_idx + 1, cols.pop(cols.index("Flip")))
    display = display[cols]

# Add latch time column
display["Latch Time"] = display["StrikeLabel"].map(
    lambda s: st.session_state.latched_strikes.get(s, {}).get("time", "")
)

# ----------------- Send CE–PE flip emails (one per detected flip) -----------------
if flip_alerts:
    for strike_num, prev_sign, curr_sign, prev_risk, curr_risk in flip_alerts:
        direction = "Bullish" if curr_sign == "Positive" else "Bearish"
        ist_now = now_ist()
        subject = f"CE–PE Risk Flip: {strike_num} → {direction} | {fmt_ist(ist_now)}"
        # prepare a concise plain-text body (plus include the row info)
        try:
            row_info = display.loc[display["StrikeLabel"].astype(str).str.contains(str(strike_num))].iloc[0].to_dict()
        except Exception:
            row_info = {}
        body_lines = [
            f"Index: {symbol}",
            f"Expiry: {selected_expiry}",
            f"Strike: {strike_num}",
            f"Flip: {prev_sign} -> {curr_sign}",
            f"Prev Risk (approx): {prev_risk}",
            f"Curr Risk: {curr_risk}",
            "",
            "Full row data:",
        ]
        for k, v in row_info.items():
            body_lines.append(f"{k}: {v}")
        body_text = "\n".join(body_lines)

        ok, err = send_email_simple(subject, body_text, to_addr=ALERT_EMAIL)
        if ok:
            st.success(f"Flip email sent: {subject}")
        else:
            st.error(f"Failed sending flip email ({strike_num}): {err}")

# -----------------
# NEW: OI_Diff sign-change detection + email alerts (per-strike)
# mirror CE–PE latch behavior but maintain separate state keys
# Improved: Show previous numeric and current numeric, use IST and better text.
# -----------------
oi_flip_alerts = []
oi_flip_symbols = []  # not used for display symbol (we color column), but kept for parity if needed

for _, row in display.iterrows():
    strike_label = row["StrikeLabel"]
    try:
        strike_num = int(str(strike_label).replace("[ATM]", "").strip())
    except Exception:
        strike_num = None

    # if OI_Diff column is present, use it; else skip
    if "OI_Diff" not in row:
        continue

    curr_oi = int(row["OI_Diff"])
    curr_oi_float = float(row.get("OI_Diff_Float", curr_oi))
    curr_sign = sign_of(curr_oi)
    prev_sign = st.session_state.oi_last_sign.get(str(strike_label))

    if prev_sign and prev_sign != "Zero" and curr_sign != "Zero" and prev_sign != curr_sign:
        # direction text
        direction = "Bullish (PE dominant)" if curr_sign == "Positive" else "Bearish (CE dominant)"
        # cooldown check
        last_sent = st.session_state.oi_last_oi_latch_time.get(str(strike_label))
        allow_send = True
        COOLDOWN = timedelta(seconds=60)
        if last_sent and (now - last_sent) < COOLDOWN:
            allow_send = False

        if allow_send:
            # prepare email
            ist_now = now_ist()
            prev_val = st.session_state.oi_last_sign.get(str(strike_label) + "_val", 0)
            subject = f"OI Diff Flip: {strike_num} → {direction} | {fmt_ist(ist_now)}"
            body_lines = [
                f"Index: {symbol}",
                f"Expiry: {selected_expiry}",
                f"Strike: {strike_num}",
                f"OI_Diff Flip: {prev_sign} -> {curr_sign}",
                f"Prev OI_Diff (approx): {prev_val}",
                f"Curr OI_Diff: {curr_oi_float}",
                f"Timestamp: {fmt_ist(ist_now)}",
                "",
                "Full row data:"
            ]
            try:
                row_info = display.loc[display["StrikeLabel"].astype(str).str.contains(str(strike_num))].iloc[0].to_dict()
            except Exception:
                row_info = {}
            for k, v in row_info.items():
                body_lines.append(f"{k}: {v}")
            body_text = "\n".join(body_lines)

            ok, err = send_email_simple(subject, body_text, to_addr=ALERT_EMAIL)
            if ok:
                st.success(f"OI Diff email sent: {subject}")
                st.session_state.oi_last_oi_latch_time[str(strike_label)] = now
                # log flip in UI
                st.session_state.oi_flip_log.append({
                    "strike": strike_label,
                    "prev_sign": prev_sign,
                    "curr_sign": curr_sign,
                    "prev_val": prev_val,
                    "curr_val": curr_oi_float,
                    "time": fmt_ist(ist_now)
                })
            else:
                st.error(f"Failed sending OI Diff email ({strike_num}): {err}")

    # update stored sign & store last numeric value too
    st.session_state.oi_last_sign[str(strike_label)] = curr_sign
    st.session_state.oi_last_sign[str(strike_label) + "_val"] = int(curr_oi_float)

# -----------------
# NEW: CE risk and PE risk flip detection + email alerts
#   - mirrors OI_Diff flip behavior but for CE_Risk and PE_Risk
# -----------------
ce_pe_risk_alerts = []
for _, row in display.iterrows():
    strike_label = row["StrikeLabel"]
    try:
        strike_num = int(str(strike_label).replace("[ATM]", "").strip())
    except Exception:
        strike_num = None

    # CE risk flip detection
    ce_curr = int(row.get("CE_Risk", 0))
    ce_sign = sign_of(ce_curr)
    ce_prev = st.session_state.ce_last_sign.get(str(strike_label))
    if ce_prev and ce_prev != "Zero" and ce_sign != "Zero" and ce_prev != ce_sign:
        last_sent = st.session_state.ce_last_latch_time.get(str(strike_label))
        allow_send = True
        COOLDOWN = timedelta(seconds=60)
        if last_sent and (now - last_sent) < COOLDOWN:
            allow_send = False
        if allow_send:
            ist_now = now_ist()
            subject = f"CE Risk Flip: {strike_num} → {'Bullish' if ce_sign=='Positive' else 'Bearish'} | {fmt_ist(ist_now)}"
            body = (
                f"Index: {symbol}\n"
                f"Expiry: {selected_expiry}\n"
                f"Strike: {strike_num}\n"
                f"CE_Risk Flip: {ce_prev} -> {ce_sign}\n"
                f"Prev CE_Risk (approx): {st.session_state.ce_last_sign.get(str(strike_label) + '_val', 0)}\n"
                f"Curr CE_Risk: {ce_curr}\n"
                f"Timestamp: {fmt_ist(ist_now)}\n\n"
            )
            try:
                row_info = display.loc[display["StrikeLabel"].astype(str).str.contains(str(strike_num))].iloc[0].to_dict()
            except Exception:
                row_info = {}
            for k, v in row_info.items():
                body += f"{k}: {v}\n"
            ok, err = send_email_simple(subject, body, to_addr=ALERT_EMAIL)
            if ok:
                st.success(f"CE Risk email sent: {subject}")
                st.session_state.ce_last_latch_time[str(strike_label)] = now
            else:
                st.error(f"Failed sending CE Risk email ({strike_num}): {err}")

    # PE risk flip detection
    pe_curr = int(row.get("PE_Risk", 0))
    pe_sign = sign_of(pe_curr)
    pe_prev = st.session_state.pe_last_sign.get(str(strike_label))
    if pe_prev and pe_prev != "Zero" and pe_sign != "Zero" and pe_prev != pe_sign:
        last_sent = st.session_state.pe_last_latch_time.get(str(strike_label))
        allow_send = True
        COOLDOWN = timedelta(seconds=60)
        if last_sent and (now - last_sent) < COOLDOWN:
            allow_send = False
        if allow_send:
            ist_now = now_ist()
            subject = f"PE Risk Flip: {strike_num} → {'Bullish (PE)' if pe_sign=='Positive' else 'Bearish (CE)'} | {fmt_ist(ist_now)}"
            body = (
                f"Index: {symbol}\n"
                f"Expiry: {selected_expiry}\n"
                f"Strike: {strike_num}\n"
                f"PE_Risk Flip: {pe_prev} -> {pe_sign}\n"
                f"Prev PE_Risk (approx): {st.session_state.pe_last_sign.get(str(strike_label) + '_val', 0)}\n"
                f"Curr PE_Risk: {pe_curr}\n"
                f"Timestamp: {fmt_ist(ist_now)}\n\n"
            )
            try:
                row_info = display.loc[display["StrikeLabel"].astype(str).str.contains(str(strike_num))].iloc[0].to_dict()
            except Exception:
                row_info = {}
            for k, v in row_info.items():
                body += f"{k}: {v}\n"
            ok, err = send_email_simple(subject, body, to_addr=ALERT_EMAIL)
            if ok:
                st.success(f"PE Risk email sent: {subject}")
                st.session_state.pe_last_latch_time[str(strike_label)] = now
            else:
                st.error(f"Failed sending PE Risk email ({strike_num}): {err}")

    # update stored CE/PE sign & numeric
    st.session_state.ce_last_sign[str(strike_label)] = ce_sign
    st.session_state.ce_last_sign[str(strike_label) + "_val"] = int(ce_curr)
    st.session_state.pe_last_sign[str(strike_label)] = pe_sign
    st.session_state.pe_last_sign[str(strike_label) + "_val"] = int(pe_curr)

# ----------------- Periodic summary every 60 seconds (unchanged cadence) -----------------
SUMMARY_INTERVAL = timedelta(seconds=60)

if (now - st.session_state.last_summary_sent) >= SUMMARY_INTERVAL:
    try:
        # ---- Compute Max PUT and CALL OI strikes ----
        max_put_strike = (
            display.loc[display["PE_OI"].idxmax(), "StrikeLabel"]
            if "PE_OI" in display.columns and not display["PE_OI"].empty
            else "N/A"
        )
        max_call_strike = (
            display.loc[display["CE_OI"].idxmax(), "StrikeLabel"]
            if "CE_OI" in display.columns and not display["CE_OI"].empty
            else "N/A"
        )

        # ---- Include OI_Diff Summary ----
        latest_oi_diff = int(display["OI_Diff"].sum()) if "OI_Diff" in display.columns else 0
        oi_direction = "Bullish (PE > CE)" if latest_oi_diff > 0 else ("Bearish (CE > PE)" if latest_oi_diff < 0 else "Neutral")
        oi_color = "#009933" if latest_oi_diff > 0 else ("#cc0000" if latest_oi_diff < 0 else "#666666")

        # ---- Display strings ----
        trend_display = trend
        atm_trend_display = atm_trend

        # ---- Rocket emoji logic ----
        rocket_symbol = "🚀" if ("Strong" in trend_display or "Strong" in atm_trend_display) else ""

        # ---- Trend color mapping (email-safe) ----
        def colorize_trend_html(text):
            color = "#999999"
            if "Bullish" in text:
                if "Risky" in text:
                    color = "#ffcc00"  # Yellow
                elif "Strong" in text:
                    color = "#00cc44"  # Bright green
                else:
                    color = "#33cc33"  # Normal green
            elif "Bearish" in text:
                if "Strong" in text:
                    color = "#ff3333"  # Bright red
                else:
                    color = "#cc0000"  # Red
            return f"<font color='{color}'><b>{text}</b></font>"

        trend_html = colorize_trend_html(trend_display)
        atm_trend_html = colorize_trend_html(atm_trend_display)

        # ---- Build header HTML ----
        header_html = f"""
        <div style="font-family: Arial; font-size: 14px;">
        <b>Summary:</b><br>
        {fmt_ist(now)} | 
        <b>🟣 Max Call OI Strike:</b> <font color='#6a0dad'><b>{max_call_strike}</b></font> | 
        <b>Spot:</b> {safe_int(spot_price)} | 
        <b>PCR (all shown):</b> {total_pcr:.2f} → {trend_html} | 
        <b>PCR (ATM ±4):</b> {atm_pcr:.2f} → {atm_trend_html} | {rocket_symbol}<br>
        <b>OI_Diff:</b> <font color='{oi_color}'><b>{latest_oi_diff}</b></font> — {oi_direction}<br>
        <b>🟢 Max Put OI Strike:</b> <font color='#009933'><b>{max_put_strike}</b></font><br>
        -----------------------------------------------------------<br>
        </div>
        """

        # ---- Build HTML table with row-level latched highlighting ----
        cols_for_table = display.columns.tolist()
        # start table
        html_table = "<table border='1' cellpadding='4' cellspacing='0' style='border-collapse: collapse; font-family: Arial; font-size:12px;'>"
        # header row
        html_table += "<thead><tr>"
        for col in cols_for_table:
            html_table += f"<th style='background:#efefef'>{col}</th>"
        html_table += "</tr></thead><tbody>"
        # rows
        for _, r in display.iterrows():
            strike_label = r["StrikeLabel"]
            latched = st.session_state.latched_strikes.get(str(strike_label))
            row_bg = ""
            if latched:
                if latched["state"] == "BULLISH":
                    row_bg = "background-color:#C6F6D5"  # light green
                elif latched["state"] == "BEARISH":
                    row_bg = "background-color:#FEB2B2"  # light red

            html_table += f"<tr style='{row_bg}'>"
            for col in cols_for_table:
                cell = r[col]
                html_table += f"<td>{cell}</td>"
            html_table += "</tr>"
        html_table += "</tbody></table>"

        full_html = header_html + html_table

        # ---- Plain text fallback ----
        plain = (
            f"Summary: {fmt_ist(now)} | "
            f"Max Call OI Strike: {max_call_strike} | "
            f"Spot: {safe_int(spot_price)} | "
            f"PCR (all shown): {total_pcr:.2f} -> {trend_display} | "
            f"PCR (ATM ±4): {atm_pcr:.2f} -> {atm_trend_display} | {rocket_symbol}\n"
            f"OI_Diff: {latest_oi_diff} -> {oi_direction}\n"
            f"Max Put OI Strike: {max_put_strike}\n"
            f"{display.to_string(index=False)}"
        )

        # ---- Send the email ----
        subject = f"CE–PE Summary Update ({fmt_ist(now)})"
        ok, err = send_email_html(subject, full_html, plain_text=plain, to_addr=ALERT_EMAIL)

        if ok:
            st.success("Summary email sent.")
            st.session_state.last_summary_sent = now
        else:
            st.error(f"Failed to send summary email: {err}")

    except Exception as e:
        st.error(f"Error preparing summary email: {e}")

# -----------------
# NEW: Trigger summary email when any tracked input changes (fires on every minor change)
# We compare a snapshot of tracked numeric fields per strike to the last_snapshot in session_state.
# If any difference detected, send a summary email immediately (uses same HTML table format).
# -----------------
try:
    # build current snapshot
    current_snapshot = {}
    for _, r in display.iterrows():
        s_label = r["StrikeLabel"]
        current_snapshot[s_label] = (
            int(r.get("CE_OI", 0)),
            int(r.get("CE_%OI", 0)),
            int(r.get("CE_LTP", 0)) if "CE_LTP" in r else 0,
            int(r.get("CE_Risk", 0)),
            int(r.get("PE_OI", 0)),
            int(r.get("PE_%OI", 0)),
            int(r.get("PE_LTP", 0)) if "PE_LTP" in r else 0,
            int(r.get("PE_Risk", 0)),
            float(r.get("OI_Diff_Float", 0.0))
        )

    changed = False
    changed_details = []
    for s_label, curr_vals in current_snapshot.items():
        prev_vals = st.session_state.last_snapshot.get(s_label)
        if prev_vals is None:
            # first time -> treat as change
            changed = True
            changed_details.append((s_label, "NEW"))
        else:
            if curr_vals != prev_vals:
                changed = True
                changed_fields = []
                names = ["CE_OI","CE_%OI","CE_LTP","CE_Risk","PE_OI","PE_%OI","PE_LTP","PE_Risk","OI_Diff"]
                for i, (p, c) in enumerate(zip(prev_vals, curr_vals)):
                    if p != c:
                        changed_fields.append((names[i], p, c))
                changed_details.append((s_label, changed_fields))

    # Update snapshot now (so we don't repeatedly detect same change) after possibly sending email
    if changed:
        # Build summary email similar to periodic
        try:
            now_send = now_ist()
                    # ---- Display strings ----
        trend_display = trend
        atm_trend_display = atm_trend

        # ---- Rocket emoji logic ----
        rocket_symbol = "🚀" if ("Strong" in trend_display or "Strong" in atm_trend_display) else ""

        # ---- Trend color mapping (email-safe) ----
        def colorize_trend_html(text):
            color = "#999999"
            if "Bullish" in text:
                if "Risky" in text:
                    color = "#ffcc00"  # Yellow
                elif "Strong" in text:
                    color = "#00cc44"  # Bright green
                else:
                    color = "#33cc33"  # Normal green
            elif "Bearish" in text:
                if "Strong" in text:
                    color = "#ff3333"  # Bright red
                else:
                    color = "#cc0000"  # Red
            return f"<font color='{color}'><b>{text}</b></font>"

        trend_html = colorize_trend_html(trend_display)
        atm_trend_html = colorize_trend_html(atm_trend_display)

        # ---- Build header HTML ----
        header_html = f"""
        <div style="font-family: Arial; font-size: 14px;">
        <b>Summary:</b><br>
        {fmt_ist(now)} | 
        <b>🟣 Max Call OI Strike:</b> <font color='#6a0dad'><b>{max_call_strike}</b></font> | 
        <b>Spot:</b> {safe_int(spot_price)} | 
        <b>PCR (all shown):</b> {total_pcr:.2f} → {trend_html} | 
        <b>PCR (ATM ±4):</b> {atm_pcr:.2f} → {atm_trend_html} | {rocket_symbol}<br>
        <b>OI_Diff:</b> <font color='{oi_color}'><b>{latest_oi_diff}</b></font> — {oi_direction}<br>
        <b>🟢 Max Put OI Strike:</b> <font color='#009933'><b>{max_put_strike}</b></font><br>

            -----------------------------------------------------------<br>
            </div>
            """

            cols_for_table = display.columns.tolist()
            html_table = "<table border='1' cellpadding='4' cellspacing='0' style='border-collapse: collapse; font-family: Arial; font-size:12px;'>"
            html_table += "<thead><tr>"
            for col in cols_for_table:
                html_table += f"<th style='background:#efefef'>{col}</th>"
            html_table += "</tr></thead><tbody>"
            for _, r in display.iterrows():
                strike_label = r["StrikeLabel"]
                latched = st.session_state.latched_strikes.get(str(strike_label))
                row_bg = ""
                if latched:
                    if latched["state"] == "BULLISH":
                        row_bg = "background-color:#C6F6D5"
                    elif latched["state"] == "BEARISH":
                        row_bg = "background-color:#FEB2B2"
                html_table += f"<tr style='{row_bg}'>"
                for col in cols_for_table:
                    cell = r[col]
                    html_table += f"<td>{cell}</td>"
                html_table += "</tr>"
            html_table += "</tbody></table>"

            full_html = header_html + html_table

            plain = f"Snapshot update {fmt_ist(now_send)}\n{display.to_string(index=False)}"

            subject = f"Update ({fmt_ist(now_send)})"
            ok, err = send_email_html(subject, full_html, plain_text=plain, to_addr=ALERT_EMAIL)
            if ok:
                st.success("Immediate summary email sent (change detected).")
                st.session_state.last_summary_sent = now
            else:
                st.error(f"Failed to send immediate snapshot summary email: {err}")

        except Exception as ee:
            st.error(f"Error when preparing immediate email: {ee}")

    # store current snapshot into session_state (regardless of changed or not)
    st.session_state.last_snapshot = current_snapshot

except Exception as e:
    st.error(f"Snapshot change detection error: {e}")

# ----------------- Rocket logic (unchanged) -----------------
atm_ce_pct = int(atm_row.get("CE_%OI", 0))
atm_pe_pct = int(atm_row.get("PE_%OI", 0))
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

# ----------------- Display header (includes OI_Diff summary) -----------------
st.markdown("---")
pcr_display = (f"{total_pcr:.2f}" if total_pcr != float("inf") else "∞")
atm_pcr_display = (f"{atm_pcr:.2f}" if atm_pcr != float("inf") else "∞")
oi_sum = int(display["OI_Diff"].sum()) if "OI_Diff" in display.columns else 0
oi_dir_summary = oi_direction_label(oi_sum)
oi_badge = "🟢" if oi_sum > 0 else ("🔴" if oi_sum < 0 else "⚪")
st.markdown(
    f"**Live Snapshot:** {fmt_ist(now)} | Spot: {safe_int(spot_price)} | "
    f"PCR (all shown): {pcr_display} → {trend} | PCR (ATM ±4): {atm_pcr_display} → {atm_trend} | "
    f"{oi_badge} OI_Diff (sum): {oi_sum} — {oi_dir_summary}"
)

st.write("### 🔍 ATM ±5 Strike Option Chain (ascending strikes)")

# ----------------- Visual highlight function (uses latched_strikes) -----------------
def latch_color(row):
    strike_label = row.get("StrikeLabel")
    latched_info = st.session_state.latched_strikes.get(str(strike_label), None)
    if latched_info:
        if latched_info["state"] == "BULLISH":
            return ["background-color: #C6F6D5"] * len(row)  # 🟢 light green
        elif latched_info["state"] == "BEARISH":
            return ["background-color: #FEB2B2"] * len(row)  # 🔴 light red
    return [""] * len(row)

# color function for OI_Diff (green if positive else red)
def color_oi_diff(val):
    try:
        v = float(val)
    except Exception:
        return ""
    color = "color: green; font-weight: 600" if v > 0 else "color: red; font-weight: 600"
    return color

# show styled table
try:
    # apply row latch color, then color only the OI_Diff column
    styled = display.style.apply(latch_color, axis=1)
    if "OI_Diff" in display.columns:
        styled = styled.applymap(color_oi_diff, subset=["OI_Diff"])
    # ensure OI_Diff visible in app display (it is included in display DataFrame)
    st.dataframe(styled, use_container_width=True, hide_index=True)
except Exception:
    # fallback if styling fails in some environments
    st.dataframe(display, use_container_width=True, hide_index=True)

# show recent OI_Diff flips log (UI)
if st.session_state.oi_flip_log:
    st.markdown("### 🔔 Recent OI_Diff Flips")
    for entry in reversed(st.session_state.oi_flip_log[-8:]):
        st.markdown(f"- **{entry['time']}** — {entry['strike']}: {entry['prev_sign']} → {entry['curr_sign']} "
                    f"({entry['prev_val']} → {entry['curr_val']})")

# Optional: Add manual reset button for latched strikes
st.markdown("### 🔄 Latch Control")
if st.button("Reset All Latched Strikes"):
    st.session_state.latched_strikes.clear()
    st.session_state.last_sign_state.clear()
    st.session_state.oi_last_sign.clear()
    st.session_state.ce_last_sign.clear()
    st.session_state.pe_last_sign.clear()
    st.success("All latched strikes reset successfully.")
    st.experimental_rerun()
