# ----------------- Max OI history chart (Altair) with ATM markers -----------------
if "max_oi_history" not in st.session_state:
    st.session_state.max_oi_history = []

timestamp = (datetime.utcnow() + timedelta(hours=5, minutes=30)).strftime("%H:%M:%S")

max_ce_oi_idx = df_filtered["CE_OI"].idxmax()
max_ce_pct_idx = df_filtered["CE_%OI"].idxmax()
max_pe_oi_idx = df_filtered["PE_OI"].idxmax()
max_pe_pct_idx = df_filtered["PE_%OI"].idxmax()

st.session_state.max_oi_history.append({
    "time": timestamp,
    "Max_CE_OI": df_filtered.loc[max_ce_oi_idx,"strikePrice"],
    "Max_CE_%OI": df_filtered.loc[max_ce_pct_idx,"strikePrice"],
    "Max_PE_OI": df_filtered.loc[max_pe_oi_idx,"strikePrice"],
    "Max_PE_%OI": df_filtered.loc[max_pe_pct_idx,"strikePrice"],
    "ATM_Strike": atm_strike
})

st.session_state.max_oi_history = st.session_state.max_oi_history[-20:]
hist_df = pd.DataFrame(st.session_state.max_oi_history).set_index("time")

pe_support_strike = df_filtered["strikePrice"].iloc[df_filtered["PE_OI"].idxmax()]
strike_step = 50 if symbol=="NIFTY" else 100
min_y = pe_support_strike - 2*strike_step
max_y = df_filtered["strikePrice"].max()

# Convert to long format
hist_long = hist_df.reset_index().melt(id_vars="time", var_name="Type", value_name="Strike")

# Base line chart for CE/PE Max OI
line_chart = alt.Chart(hist_long[hist_long["Type"]!="ATM_Strike"]).mark_line(point=True).encode(
    x=alt.X('time:T', title='Time (IST)'),
    y=alt.Y('Strike:Q', scale=alt.Scale(domain=[min_y, max_y])),
    color=alt.Color('Type:N', scale=alt.Scale(domain=['Max_CE_OI','Max_CE_%OI','Max_PE_OI','Max_PE_%OI'],
                                              range=['green','lightgreen','red','orange'])),
    tooltip=['time','Type','Strike']
)

# ATM strike markers
atm_chart = alt.Chart(hist_df.reset_index()).mark_point(filled=True, shape='triangle-up', size=100, color='blue').encode(
    x='time:T',
    y='ATM_Strike:Q',
    tooltip=['time','ATM_Strike']
)

# Combine charts
final_chart = line_chart + atm_chart
final_chart = final_chart.properties(
    height=400,
    width=800,
    title="📈 Max CE/PE OI & %OI Strike Evolution (Last 20 snapshots, ATM ±6 strikes, ATM marker in blue)"
).interactive()

st.altair_chart(final_chart, use_container_width=True)
