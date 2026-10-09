"""Natural gas consumption by sector in Finland, Estonia, Latvia and Lithuania.

Data: Eurostat complete energy balances nrg_bal_c (yearly).
Run with:  streamlit run app.py
"""
import time

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

st.set_page_config(page_title="FinBalt Gas Use by Sector", page_icon="🏭", layout="wide")

EUROSTAT_URL = ("https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/"
                "data/nrg_bal_c")
GEO = {"FI": "Finland", "EE": "Estonia", "LV": "Latvia", "LT": "Lithuania"}
COUNTRY_COLS = list(GEO.values())
START_YEAR = 2015
# (nrg_bal code, label) for natural gas (siec G3000)
SECTORS = [
    ("FC_OTH_HH_E", "Households"),
    ("FC_IND_E", "Industry"),
    ("FC_NE", "Non-energy use (feedstock)"),
    ("TI_EHG_E", "Electricity & heat"),
]
OTHER = "Other"
SECTOR_COLS = [s for _, s in SECTORS] + [OTHER]
SECTOR_COLORS = {
    "Households": "#17becf",
    "Industry": "#7f7f7f",
    "Non-energy use (feedstock)": "#bcbd22",
    "Electricity & heat": "#e45756",
    OTHER: "#c7c7c7",
}


# --------------------------------------------------------- Eurostat fetch
def parse_jsonstat(js):
    """JSON-stat 2.0 dataset -> long DataFrame (one column per dimension + value)."""
    ids, sizes = js["id"], js["size"]
    labels = []
    for d in ids:
        index = js["dimension"][d]["category"]["index"]
        if isinstance(index, list):
            index = {code: i for i, code in enumerate(index)}
        labels.append({pos: code for code, pos in index.items()})
    values = js.get("value", {})
    if isinstance(values, list):
        values = {i: v for i, v in enumerate(values) if v is not None}
    rows = []
    for flat, v in values.items():
        rest, coords = int(flat), []
        for size in reversed(sizes):
            rest, pos = divmod(rest, size)
            coords.append(pos)
        coords.reverse()
        rows.append([labels[i][p] for i, p in enumerate(coords)] + [float(v)])
    return pd.DataFrame(rows, columns=ids + ["value"])


@st.cache_data(ttl=24 * 3600, show_spinner="Loading Eurostat data...")
def fetch_sector_balance():
    """Yearly natural gas use per sector and country (TWh) from Eurostat nrg_bal_c."""
    params = [("format", "JSON"), ("lang", "EN"), ("siec", "G3000"), ("unit", "GWH"),
              ("sinceTimePeriod", str(START_YEAR))]
    params += [("geo", g) for g in GEO]
    params += [("nrg_bal", c) for c, _ in SECTORS] + [("nrg_bal", "GIC")]
    err = "unknown error"
    for attempt in range(3):
        try:
            r = requests.get(EUROSTAT_URL, params=params, timeout=60)
            if r.status_code == 200:
                break
            err = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            err = type(e).__name__
        time.sleep(3 * (attempt + 1))
    else:
        raise RuntimeError(err)

    df = parse_jsonstat(r.json())
    df = df[df["geo"].isin(GEO)]
    wide = df.pivot_table(index=["geo", "time"], columns="nrg_bal",
                          values="value", aggfunc="sum")
    wide = wide.reindex(columns=[c for c, _ in SECTORS] + ["GIC"])
    wide = wide[wide["GIC"].notna()]  # skip years with no balance yet

    out = pd.DataFrame(index=wide.index)
    for code, name in SECTORS:
        out[name] = wide[code].fillna(0.0) / 1000  # GWh -> TWh
    out[OTHER] = wide["GIC"] / 1000 - out[[s for _, s in SECTORS]].sum(axis=1)
    out["Total"] = wide["GIC"] / 1000
    out = out.reset_index().rename(columns={"time": "Year"})
    out["Country"] = out["geo"].map(GEO)
    return out.drop(columns="geo")


def stacked_bars(df, unit, hover_fmt):
    fig = go.Figure()
    for s in SECTOR_COLS:
        fig.add_trace(go.Bar(x=df.index, y=df[s], name=s, marker_color=SECTOR_COLORS[s],
                             texttemplate="%{y:.1f}", textposition="inside",
                             hovertemplate=f"%{{y:{hover_fmt}}} {unit}"))
    fig.update_layout(barmode="relative", template="plotly_white", height=460,
                      legend_title_text="", hovermode="x unified",
                      legend=dict(orientation="h", y=1.1, x=0))
    fig.update_xaxes(type="category")
    return fig


# ---------------------------------------------------------------------- UI
st.title("🏭 FinBalt Gas Consumption by Sector")
st.markdown(
    "Yearly natural gas use in **Finland, Estonia, Latvia and Lithuania** split into "
    "households, industry, feedstock, electricity & heat and other uses "
    "(Eurostat energy balances)."
)

if st.sidebar.button("Clear cache & refresh 🔄"):
    st.cache_data.clear()
    st.rerun()
st.sidebar.caption("Data is cached for 24 hours.")

try:
    sectors = fetch_sector_balance()
except Exception as e:
    st.error(f"Data could not be loaded from Eurostat ({e}). Press refresh to retry.")
    st.stop()
if sectors.empty:
    st.error("Eurostat returned no data.")
    st.stop()

years = sorted(sectors["Year"].unique())

# --- One year, all countries
st.subheader("Consumption by sector and country")
c1, c2 = st.columns(2)
sel_year = c1.selectbox("Year:", years, index=len(years) - 1)
as_share = c2.radio("Show as:", ["TWh", "% of total"], horizontal=True) == "% of total"

yr = (sectors[sectors["Year"] == sel_year].set_index("Country")
      .reindex(COUNTRY_COLS).dropna(how="all"))
unit = "%" if as_share else "TWh"
plot_y = yr[SECTOR_COLS].div(yr["Total"], axis=0) * 100 if as_share else yr[SECTOR_COLS]
fig = stacked_bars(plot_y, unit, ".1f")
fig.update_yaxes(title_text=f"{sel_year} ({unit})", tickformat=".0f")
st.plotly_chart(fig, use_container_width=True)

st.markdown("---")

# --- One country, all years
st.subheader("Development over time")
sel_country = st.selectbox("Country:", COUNTRY_COLS)
hist = sectors[sectors["Country"] == sel_country].set_index("Year").sort_index()
fig_h = stacked_bars(hist[SECTOR_COLS], "TWh", ".2f")
fig_h.update_traces(texttemplate=None)
fig_h.update_yaxes(title_text=f"{sel_country} (TWh / year)", tickformat=".1f")
st.plotly_chart(fig_h, use_container_width=True)

st.markdown("---")

# --- Table
st.subheader("Table (TWh)")
table = sectors.set_index(["Country", "Year"])[SECTOR_COLS + ["Total"]]
table = table.reindex(COUNTRY_COLS, level=0)
st.dataframe(table.style.format("{:.2f}"), use_container_width=True)
st.download_button(
    "Download CSV 📥",
    table.round(3).to_csv().encode("utf-8"),
    file_name="finbalt_gas_consumption_by_sector.csv",
    mime="text/csv",
)

with st.expander("Method and caveats"):
    st.markdown(
        """
**Source:** Eurostat complete energy balances (`nrg_bal_c`), natural gas
(`G3000`), GWh converted to TWh, yearly.

- **Households:** final consumption, households (`FC_OTH_HH_E`).
- **Industry:** final consumption, industry sector, energy use (`FC_IND_E`).
- **Non-energy use (feedstock):** gas used as raw material, e.g. for fertilisers
  (`FC_NE`). Mostly the chemical industry.
- **Electricity & heat:** transformation input to power plants, CHP and heat-only
  plants, including autoproducers (`TI_EHG_E`).
- **Other:** gross inland consumption (`GIC`) minus the above: services, transport,
  energy sector own use, other transformation, distribution losses and statistical
  differences.
- Confidential or missing values count as 0, which moves them into *Other*.
- Eurostat publishes the balances with about a one-year lag.
- Energy units are on a net calorific value basis, so totals are roughly 10 % lower
  than gross calorific value figures such as ENTSOG flows.
        """
    )
