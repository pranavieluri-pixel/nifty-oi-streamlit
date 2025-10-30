# pages41_Option.py
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
    st.session_state.last_summary_sent = datetime.min

# ----------------- Flip detection: compare current vs ~2 minutes earlier -----------------
now = datetime.now()
FLIP_LOOKBACK = timedelta(seconds=120)  # 2 minutes
PRUNE_OLDER_THAN = timedelta(minutes=5)  # keep short history

flip_alerts = []   # list of (strike, prev_sign, curr_sign, prev_risk, curr_risk)
flip_symbols = []  # aligned with display rows

# iterate rows and maintain per-strike time-series
for _, row in display.iterrows():
    strike_label = row["StrikeLabel"]
    strike = int(strike_label.replace("[ATM]", "").strip())
    curr_risk = int(row["CE_PE_Diff"])
    hist = st.session_state.flip_history.get(str(strike), [])  # list of dicts

    # append current sample
    hist.append({"time": now, "risk": curr_risk})
    # prune old samples
    hist = [h for h in hist if (now - h["time"]) <= PRUNE_OLDER_THAN]
    st.session_state.flip_history[str(strike)] = hist

    # find most recent sample older than or equal to lookback (i.e., ~2 minutes ago)
    prev_candidates = [h for h in hist if h["time"] <= (now - FLIP_LOOKBACK)]
    prev_entry = prev_candidates[-1] if prev_candidates else None

    # determine signs
    def sign_of(x):
        if x > 0:
            return "Positive"
        elif x < 0:
            return "Negative"
        else:
            return "Zero"

    curr_sign = sign_of(curr_risk)
    prev_sign = sign_of(prev_entry["risk"]) if prev_entry else None

    # default symbol
    symbol = "➖"

    # flip detection only if prev_entry exists and prev_sign <-> curr_sign flip between Positive and Negative
    if prev_entry and prev_sign and curr_sign and prev_sign != "Zero" and curr_sign != "Zero":
        if (prev_sign == "Positive" and curr_sign == "Negative") or (prev_sign == "Negative" and curr_sign == "Positive"):
            # avoid duplicate email for the same strike & direction within the last 2 minutes
            last_sent = st.session_state.last_flip_time.get(str(strike))
            allow_send = True
            if last_sent and (now - last_sent) < FLIP_LOOKBACK:
                allow_send = False

            # register flip symbol and prepare alert
            if curr_sign == "Positive":
                symbol = "✅"  # Bearish -> Bullish
            else:
                symbol = "🔴"  # Bullish -> Bearish

            if allow_send:
                flip_alerts.append((strike, prev_sign, curr_sign, prev_entry["risk"], curr_risk))
                # record last sent moment to prevent duplicate emails for same flip within lookback
                st.session_state.last_flip_time[str(strike)] = now

    flip_symbols.append(symbol)

# attach Flip column
display["Flip"] = flip_symbols
# reorder Flip right after StrikeLabel
cols = display.columns.tolist()
if "Flip" in cols and "StrikeLabel" in cols:
    strike_idx = cols.index("StrikeLabel")
    cols.insert(strike_idx + 1, cols.pop(cols.index("Flip")))
    display = display[cols]

# ----------------- Send flip emails (one per detected flip) -----------------
if flip_alerts:
    for strike, prev_sign, curr_sign, prev_risk, curr_risk in flip_alerts:
        direction = "Bullish" if curr_sign == "Positive" else "Bearish"
        subject = f"CE–PE Risk Flip: {strike} → {direction}"
        # prepare a concise plain-text body (plus include the row info)
        row_info = display.loc[display["StrikeLabel"].str.contains(str(strike))].iloc[0].to_dict()
        body_lines = [
            f"Index: {symbol}",
            f"Expiry: {selected_expiry}",
            f"Strike: {strike}",
            f"Flip: {prev_sign} -> {curr_sign}",
            f"Prev Risk (2+ min ago): {prev_risk}",
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
            st.error(f"Failed sending flip email ({strike}): {err}")

# ----------------- Periodic summary every 60 seconds (Option A) -----------------
SUMMARY_INTERVAL = timedelta(seconds=60)
if (now - st.session_state.last_summary_sent) >= SUMMARY_INTERVAL:
    try:
        # --- Calculate summary header values ---
        max_put_strike = display.loc[display["Put OI"] == display["Put OI"].max(), "Strike"].iloc[0]
        max_call_strike = display.loc[display["Call OI"] == display["Call OI"].max(), "Strike"].iloc[0]
        spot = spot_price
        pcr_all = pcr_all_str
        pcr_atm = pcr_atm_str
        summary_trend = pcr_signal  # Example: 🔴🚀 Strong Bearish

        # --- Prepare color-coded HTML header ---
        summary_header = f"""
        <div style="font-family:Arial; font-size:14px; line-height:1.5;">
        <b>Summary:</b> {now.strftime('%Y-%m-%d %H:%M:%S')} |
        <span style='color:#7B68EE;'>🟣 Max Call OI Strike:</span> <b>{max_call_strike}</b> |
        <span style='color:#1E90FF;'>Spot:</span> <b>{spot}</b> |
        <span style='color:#000;'>PCR (all shown):</span> <b>{pcr_all}</b> |
        <span style='color:#000;'>PCR (ATM ±4):</span> <b>{pcr_atm}</b> |
        <b>{summary_trend}</b><br>
        <span style='color:#32CD32;'>🟢 Max Put OI Strike:</span> <b>{max_put_strike}</b><br>
        <hr style="border:0; border-top:1px solid #aaa;">
        </div>
        """

        # --- Convert display DataFrame to HTML table ---
        html_table = display.to_html(index=False, escape=False, border=1)

        # --- Combine header and table ---
        html_content = f"{summary_header}{html_table}"

        # --- Subject and plain fallback ---
        subject = "CE–PE Summary Update"
        plain = (
            f"CE–PE Summary Update ({now.strftime('%Y-%m-%d %H:%M:%S')})\n\n"
            f"Max Call OI Strike: {max_call_strike}\n"
            f"Max Put OI Strike: {max_put_strike}\n"
            f"Spot: {spot}\n"
            f"PCR (all shown): {pcr_all}\n"
            f"PCR (ATM ±4): {pcr_atm}\n"
            f"Trend: {summary_trend}\n\n"
            f"See HTML version for full table."
        )

        ok, err = send_email_html(subject, html_content, plain_text=plain, to_addr=ALERT_EMAIL)
        if ok:
            st.success("Summary email sent.")
            st.session_state.last_summary_sent = now
        else:
            st.error(f"Failed to send summary email: {err}")

    except Exception as e:
        st.error(f"Error preparing summary email: {e}")


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

# ----------------- Display -----------------
st.markdown("---")
pcr_display = (f"{total_pcr:.2f}" if total_pcr != float("inf") else "∞")
atm_pcr_display = (f"{atm_pcr:.2f}" if atm_pcr != float("inf") else "∞")
st.markdown(
    f"**Live Snapshot:** {now.strftime('%Y-%m-%d %H:%M:%S')} | "
    f"Spot: {safe_int(spot_price)} | "
    f"PCR (all shown): {pcr_display} → {trend} | "
    f"PCR (ATM ±4): {atm_pcr_display} → {atm_trend} | {rocket_symbol} {rocket_text}"
)

st.write("### 🔍 ATM ±5 Strike Option Chain (ascending strikes)")
# colorize Flip column visually (simple):
def highlight_flip(row):
    f = row.get("Flip", "")
    if f == "✅":
        return ["background-color: #b6ffb6"] * len(row)
    if f == "🔴":
        return ["background-color: #ffb6b6"] * len(row)
    return [""] * len(row)

# show styled table
try:
    styled = display.style.apply(highlight_flip, axis=1)
    st.dataframe(styled, use_container_width=True, hide_index=True)
except Exception:
    # fallback if styling fails in some environments
    st.dataframe(display, use_container_width=True, hide_index=True)
