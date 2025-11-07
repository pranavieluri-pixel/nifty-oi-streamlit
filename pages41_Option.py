#!/usr/bin/env python3
import requests
import pandas as pd
import streamlit as st
from datetime import datetime, timedelta, timezone
from streamlit_autorefresh import st_autorefresh

# ----------------- Config -----------------
st.set_page_config(page_title="NIFTY & BANKNIFTY Option Chain — On-screen Test (OI_Diff + Flips)", layout="wide")
_ = st_autorefresh(interval=30000, limit=None, key="refresh_counter")  # 30s auto-refresh

# NSE fetch settings
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/"
}

# Time helper: IST timezone
IST = timezone(timedelta(hours=5, minutes=30))
def now_ist():
    return datetime.now(IST)
def fmt_ist(dt: datetime):
    return dt.strftime("%d-%b-%Y %H:%M:%S") + " IST"

# ----------------- Helpers -----------------
def fetch_option_chain(symbol: str):
    url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
    s = requests.Session()
    # initial GET to set cookies (best-effort, ignore failures)
    try:
        s.get("https://www.nseindia.com", headers=HEADERS, timeout=5)
        r = s.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"Failed to fetch option chain: {e}")
        return {}

def safe_int(x):
    try:
        return int(round(float(x)))
    except Exception:
        return 0

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

# ----------------- UI controls -----------------
st.title("📊 NIFTY / BANKNIFTY Option Chain — On-screen Test (OI_Diff + Flips)")
symbol = st.radio("Select Index", ["NIFTY", "BANKNIFTY"], horizontal=True)
if st.button("♻️ Manual Refresh"):
    st.experimental_rerun()

# ----------------- Fetch data -----------------
raw = fetch_option_chain(symbol)
if not raw:
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
    st.error("Could not find expiry dates in NSE response.")
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

# ----------------- PCRs (for header display) -----------------
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

# guard missing columns
for col in ("PE_OI","CE_OI","PE_%OI","CE_%OI"):
    if col not in display.columns:
        display[col] = 0

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

# reorder columns so OI_Diff visible early
ordered_cols = [
    "OI_Diff","OI_Diff_Float","OI_Diff_Dir","CE_PE_Diff","StrikeLabel",
    "CE_%OI","PE_%OI","CE_Risk","CE_LTP","SPOT","PE_LTP","CE_OI","PE_Risk","PE_OI"
]
ordered_cols = [c for c in ordered_cols if c in display.columns]
display = display[ordered_cols].copy()

# cast ints where appropriate
for c in ["CE_OI","CE_%OI","CE_Risk","CE_PE_Diff","CE_LTP","SPOT","PE_LTP","PE_Risk","PE_%OI","PE_OI"]:
    if c in display.columns:
        display[c] = display[c].fillna(0).astype(int)

# ----------------- Session state initialization -----------------
if "oi_last_sign" not in st.session_state:
    st.session_state.oi_last_sign = {}          # strikeLabel -> "Positive"/"Negative"/"Zero"
if "oi_last_val" not in st.session_state:
    st.session_state.oi_last_val = {}           # strikeLabel -> numeric float (OI_Diff_Float)
if "recent_flips" not in st.session_state:
    st.session_state.recent_flips = []          # list of flip strings (most recent first)
if "last_sent" not in st.session_state:
    # independent cooldown timers for toasts/logging (seconds)
    st.session_state.last_sent = {"OI_DIFF": datetime.min.replace(tzinfo=IST),
                                  "CE_RISK": datetime.min.replace(tzinfo=IST),
                                  "PE_RISK": datetime.min.replace(tzinfo=IST)}
# cooldown (seconds) per alert type - used to throttle toasts/log entries
ALERT_COOLDOWNS = {"OI_DIFF": 60, "CE_RISK": 120, "PE_RISK": 120}

# latched strike presentation
if "latched_strikes" not in st.session_state:
    st.session_state.latched_strikes = {}  # strikeLabel -> {"state": "BULLISH"/"BEARISH", "time": "HH:MM:SS"}

# last_sign for CE/PE risk flips (for completeness)
if "ce_last_sign" not in st.session_state:
    st.session_state.ce_last_sign = {}
if "pe_last_sign" not in st.session_state:
    st.session_state.pe_last_sign = {}

# ----------------- Detect OI_Diff flips and update recent_flips + toast -----------------
now = now_ist()
toast_supported = hasattr(st, "toast")

for _, row in display.iterrows():
    s_label = str(row["StrikeLabel"])
    curr_val = float(row.get("OI_Diff_Float", 0.0))
    curr_sign = sign_of(curr_val)
    prev_sign = st.session_state.oi_last_sign.get(s_label)
    prev_val = st.session_state.oi_last_val.get(s_label, 0.0)

    # detect flip Positive <-> Negative only (ignore Zero intermediates)
    if prev_sign and prev_sign != "Zero" and curr_sign != "Zero" and prev_sign != curr_sign:
        # check cooldown for OI_DIFF to avoid repeated notifications
        last_sent = st.session_state.last_sent.get("OI_DIFF", datetime.min.replace(tzinfo=IST))
        if (now - last_sent).total_seconds() >= ALERT_COOLDOWNS["OI_DIFF"]:
            # create flip entry string
            entry_time = fmt_ist(now)
            entry = f"{entry_time} — {s_label}: {prev_sign} → {curr_sign} ({int(prev_val)} → {curr_val})"
            # prepend to recent flips and keep only 4 most recent
            st.session_state.recent_flips.insert(0, entry)
            st.session_state.recent_flips = st.session_state.recent_flips[:4]
            # log last sent time
            st.session_state.last_sent["OI_DIFF"] = now
            # also update latched_strikes (for table coloring)
            latched_state = "BULLISH" if curr_sign == "Positive" else "BEARISH"
            st.session_state.latched_strikes[s_label] = {"state": latched_state, "time": now.strftime("%H:%M:%S")}
            # show toast (if available) or ephemeral success
            try:
                if toast_supported:
                    # small toast with concise message
                    st.toast(f"OI_Diff Flip: {s_label} {prev_sign}→{curr_sign} ({int(prev_val)}→{curr_val})", icon="🔔")
                else:
                    st.success(f"OI_Diff Flip: {s_label} {prev_sign}→{curr_sign} ({int(prev_val)}→{curr_val})")
            except Exception:
                # fallback
                st.info(f"Flip: {s_label} {prev_sign}→{curr_sign}")

    # always update last seen sign & numeric val
    st.session_state.oi_last_sign[s_label] = curr_sign
    st.session_state.oi_last_val[s_label] = curr_val

# ----------------- (Optional) CE/PE risk flip detection -> update latched & optional toasts -----------------
# We detect CE/PE flips to color rows and (optionally) show a toast — uses their own cooldowns.
for _, row in display.iterrows():
    s_label = str(row["StrikeLabel"])

    # CE risk
    ce_curr = int(row.get("CE_Risk", 0))
    ce_sign = sign_of(ce_curr)
    ce_prev = st.session_state.ce_last_sign.get(s_label)
    if ce_prev and ce_prev != "Zero" and ce_sign != "Zero" and ce_prev != ce_sign:
        last_sent = st.session_state.last_sent.get("CE_RISK", datetime.min.replace(tzinfo=IST))
        if (now - last_sent).total_seconds() >= ALERT_COOLDOWNS["CE_RISK"]:
            st.session_state.last_sent["CE_RISK"] = now
            # update latched_strikes (CE/PE flips will set BULLISH/BEARISH)
            latched_state = "BULLISH" if ce_sign == "Positive" else "BEARISH"
            st.session_state.latched_strikes[s_label] = {"state": latched_state, "time": now.strftime("%H:%M:%S")}
            try:
                if toast_supported:
                    st.toast(f"CE_Risk Flip: {s_label} {ce_prev}→{ce_sign}", icon="⚠️")
                else:
                    st.info(f"CE_Risk Flip: {s_label} {ce_prev}→{ce_sign}")
            except Exception:
                pass
    st.session_state.ce_last_sign[s_label] = ce_sign

    # PE risk
    pe_curr = int(row.get("PE_Risk", 0))
    pe_sign = sign_of(pe_curr)
    pe_prev = st.session_state.pe_last_sign.get(s_label)
    if pe_prev and pe_prev != "Zero" and pe_sign != "Zero" and pe_prev != pe_sign:
        last_sent = st.session_state.last_sent.get("PE_RISK", datetime.min.replace(tzinfo=IST))
        if (now - last_sent).total_seconds() >= ALERT_COOLDOWNS["PE_RISK"]:
            st.session_state.last_sent["PE_RISK"] = now
            latched_state = "BULLISH" if pe_sign == "Positive" else "BEARISH"
            st.session_state.latched_strikes[s_label] = {"state": latched_state, "time": now.strftime("%H:%M:%S")}
            try:
                if toast_supported:
                    st.toast(f"PE_Risk Flip: {s_label} {pe_prev}→{pe_sign}", icon="⚠️")
                else:
                    st.info(f"PE_Risk Flip: {s_label} {pe_prev}→{pe_sign}")
            except Exception:
                pass
    st.session_state.pe_last_sign[s_label] = pe_sign

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

# color function for OI_Diff column
def color_oi_diff(val):
    try:
        v = float(val)
    except Exception:
        return ""
    return "color: green; font-weight: 600" if v > 0 else "color: red; font-weight: 600"

# show styled table and also a separate OI_Diff column + direction badges above the table
try:
    # show top-level per-strike OI_Diff badges in a compact horizontal layout (for readability)
    cols_show = ["StrikeLabel", "OI_Diff", "OI_Diff_Float", "OI_Diff_Dir", "CE_LTP", "PE_LTP", "CE_OI", "PE_OI"]
    cols_show = [c for c in cols_show if c in display.columns]
    # Create small summary table for quick glance (as HTML badges)
    rows_html = []
    for _, r in display.iterrows():
        badge = "🟢" if r["OI_Diff"] > 0 else ("🔴" if r["OI_Diff"] < 0 else "⚪")
        dir_short = "Bullish" if r["OI_Diff"] > 0 else ("Bearish" if r["OI_Diff"] < 0 else "Neutral")
        rows_html.append(
            f"<div style='display:flex; gap:12px; align-items:center; padding:6px 4px; border-bottom:1px solid #eee;'>"
            f"<div style='width:120px'><b>{r['StrikeLabel']}</b></div>"
            f"<div style='width:90px'><b>{r['OI_Diff']}</b> ({r['OI_Diff_Float']})</div>"
            f"<div style='width:160px'>{badge} <i>{dir_short}</i></div>"
            f"<div style='width:110px'>CE:{r.get('CE_LTP', '')} / {int(r.get('CE_OI',0))}</div>"
            f"<div style='width:110px'>PE:{r.get('PE_LTP', '')} / {int(r.get('PE_OI',0))}</div>"
            f"</div>"
        )
    summary_html = "<div style='font-family: Arial; font-size:13px;'>" + "".join(rows_html) + "</div>"
    st.markdown(summary_html, unsafe_allow_html=True)

    # Styled full DataFrame
    styled = display.style.apply(latch_color, axis=1)
    if "OI_Diff" in display.columns:
        styled = styled.applymap(color_oi_diff, subset=["OI_Diff"])
    st.dataframe(styled, use_container_width=True, hide_index=True)

except Exception:
    st.dataframe(display, use_container_width=True, hide_index=True)

# ----------------- Recent flips panel (highlight newest entry + keep last 4) -----------------
st.markdown("### 🔔 Recent OI_Diff Flips (latest first)")

recent = st.session_state.recent_flips or []
if recent:
    # Use HTML box with subtle background and highlight the newest
    html_lines = []
    # highlight newest (index 0) with a stronger background
    for idx, line in enumerate(recent):
        if idx == 0:
            html_lines.append(f"<div style='background:#FFF4C2; padding:8px; margin-bottom:6px; border-radius:6px;'><b>{line}</b></div>")
        else:
            html_lines.append(f"<div style='background:#F5F7FF; padding:6px; margin-bottom:4px; border-radius:6px;'>{line}</div>")
    flips_html = "<div style='font-family: Arial; font-size:13px;'>" + "".join(html_lines) + "</div>"
    st.markdown(flips_html, unsafe_allow_html=True)
else:
    st.info("No OI_Diff flips detected yet in this session.")

# ----------------- Latch control / reset -----------------
st.markdown("### 🔄 Latch Control")
if st.button("Reset All Latched Strikes & Flips (UI only)"):
    st.session_state.latched_strikes.clear()
    st.session_state.last_sent = {"OI_DIFF": datetime.min.replace(tzinfo=IST),
                                 "CE_RISK": datetime.min.replace(tzinfo=IST),
                                 "PE_RISK": datetime.min.replace(tzinfo=IST)}
    st.session_state.oi_last_sign.clear()
    st.session_state.oi_last_val.clear()
    st.session_state.recent_flips.clear()
    st.success("Cleared latched strikes and recent flips for this session.")
    st.experimental_rerun()
