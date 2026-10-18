"""Natural gas consumption by sector in Finland, Estonia, Latvia and Lithuania.

Data: Eurostat complete energy balances nrg_bal_c (yearly). Years that Eurostat
has not published yet are filled with preliminary national energy balances
where available: CSP Latvia (ENB060) and Statistics Lithuania (S1R099).
Run with:  streamlit run app.py
"""
import time
import xml.etree.ElementTree as ET

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
EUROSTAT = "Eurostat"
TJ_PER_TWH = 3600.0

# National sources (preliminary), used only for years Eurostat does not have yet.
LV_URL = "https://data.stat.gov.lv/api/v1/en/OSP_PUB/START/NOZ/EN/ENB/ENB060"
LV_SOURCE = "CSP Latvia ENB060 (preliminary)"
LT_URL = ("https://osp-rs.stat.gov.lt/ords/ipospp/ospp/rest_xml/data/"
          "S1R099_M8020305")
LT_SOURCE = "Statistics Lithuania S1R099 (preliminary)"
LT_GAS = "2000"  # fuel code: natural gas
SDMX_G = "{http://www.sdmx.org/resources/sdmxml/schemas/v2_1/data/generic}"
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
    out["Source"] = EUROSTAT
    return out.drop(columns="geo")


# ------------------------------------------------ national preliminary data
def _get(url, **kwargs):
    """HTTP request with retries. Raises RuntimeError if all attempts fail."""
    err = "unknown error"
    for attempt in range(3):
        try:
            r = requests.request(url=url, timeout=90, **kwargs)
            if r.status_code == 200:
                return r
            err = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            err = type(e).__name__
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(err)


def _national_frame(country, source, years):
    """years: {year: {"Households": TJ, ..., "Total": TJ}} -> rows in TWh."""
    rows = []
    for year, v in sorted(years.items()):
        if not v.get("Total"):
            continue  # no balance for this year
        row = {s: v.get(s, 0.0) / TJ_PER_TWH for _, s in SECTORS}
        row["Total"] = v["Total"] / TJ_PER_TWH
        row[OTHER] = row["Total"] - sum(row[s] for _, s in SECTORS)
        row.update(Year=str(year), Country=country, Source=source)
        rows.append(row)
    return pd.DataFrame(rows)


@st.cache_data(ttl=24 * 3600, show_spinner="Loading CSP Latvia data...")
def fetch_latvia(since):
    """Natural gas balance from CSP Latvia ENB060 (TJ), years >= since."""
    meta = _get(LV_URL, method="GET").json()
    times = next(v["values"] for v in meta["variables"] if v["code"] == "TIME")
    times = [t for t in times if int(t) >= since]
    if not times:
        return _national_frame("Latvia", LV_SOURCE, {})
    query = {
        "query": [
            {"code": "ENERGY_RES", "selection": {"filter": "item", "values": ["NG"]}},
            {"code": "ContentsCode", "selection": {"filter": "item", "values": ["ENB060"]}},
            {"code": "INDICATOR", "selection": {"filter": "item", "values":
                ["GEC", "T", "HH", "FC_IND_CON"]}},
            {"code": "TIME", "selection": {"filter": "item", "values": times}},
        ],
        "response": {"format": "json-stat2"},
    }
    df = parse_jsonstat(_get(LV_URL, method="POST", json=query).json())
    w = df.pivot_table(index="TIME", columns="INDICATOR", values="value", aggfunc="sum")
    years = {}
    for year, r in w.iterrows():
        g = r.get
        years[year] = {
            "Households": g("HH", 0.0) or 0.0,
            "Industry": g("FC_IND_CON", 0.0) or 0.0,  # incl. construction, as Eurostat
            "Electricity & heat": -(g("T", 0.0) or 0.0),  # transformation input (< 0)
            "Total": g("GEC", 0.0) or 0.0,
        }
    return _national_frame("Latvia", LV_SOURCE, years)


@st.cache_data(ttl=24 * 3600, show_spinner="Loading Statistics Lithuania data...")
def fetch_lithuania(since):
    """Natural gas balance from Statistics Lithuania S1R099 (TJ), years >= since."""
    r = _get(LT_URL, method="GET", params={"startPeriod": str(since)})
    root = ET.fromstring(r.content)
    # indicator -> sector; several indicators can add up to one sector
    mapping = {
        "nu": "Households",
        "pramone": "Industry", "statyba": "Industry",
        "neenergetika": "Non-energy use (feedstock)",
        "pagrveiklelektrine": "Electricity & heat",
        "pagrveiklkatiline": "Electricity & heat",
        "pramimelektrinese": "Electricity & heat",
        "pramimkatiline": "Electricity & heat",
        "bendrsunaudojimas": "Total",
    }
    years = {}
    for obs in root.iter(SDMX_G + "Obs"):
        key = {v.get("id"): v.get("value")
               for v in obs.find(SDMX_G + "ObsKey").iter(SDMX_G + "Value")}
        if (key.get("kurasM8020305_1") != LT_GAS
                or key.get("MATVNT") != "terdzauliai_tj"):
            continue
        sector = mapping.get(key.get("energetika_rodiklM802030"))
        if sector is None:
            continue
        try:
            value = float(obs.find(SDMX_G + "ObsValue").get("value"))
        except (TypeError, ValueError, AttributeError):
            continue
        if value != value:  # NaN
            continue
        y = years.setdefault(key["LAIKOTARPIS"], {})
        y[sector] = y.get(sector, 0.0) + value
    return _national_frame("Lithuania", LT_SOURCE, years)


NATIONAL = {"Latvia": fetch_latvia, "Lithuania": fetch_lithuania}


def add_national(sectors):
    """Append national rows for years after each country's last Eurostat year."""
    frames, failed = [sectors], []
    for country, fetch in NATIONAL.items():
        have = sectors.loc[sectors["Country"] == country, "Year"]
        since = int(have.max()) + 1 if not have.empty else START_YEAR
        try:
            frames.append(fetch(since))
        except Exception as e:
            failed.append(f"{country}: {e}")
    out = pd.concat([f for f in frames if not f.empty], ignore_index=True)
    return out, failed


def year_label(year, prelim_years):
    return f"{year}*" if year in prelim_years else year


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
    "(Eurostat energy balances; the latest year from national statistics where "
    "Eurostat has not published it yet)."
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

sectors, nat_failed = add_national(sectors)
if nat_failed:
    st.warning("Preliminary national data could not be loaded, so only Eurostat "
               "years are shown for: " + "; ".join(nat_failed))
prelim = sectors[sectors["Source"] != EUROSTAT]
prelim_years = set(prelim["Year"])

years = sorted(sectors["Year"].unique())


def prelim_note(year):
    """Caption naming the countries whose figures for this year are preliminary."""
    rows = prelim[prelim["Year"] == year]
    if rows.empty:
        return
    countries = ", ".join(rows["Country"])
    missing = [c for c in COUNTRY_COLS
               if c not in set(sectors.loc[sectors["Year"] == year, "Country"])]
    text = (f"\\* {year}: preliminary national data for {countries} "
            "(not yet in Eurostat; may be revised).")
    if missing:
        text += f" No data yet for {', '.join(missing)}."
    st.caption(text)


# --- One year, all countries
st.subheader("Consumption by sector and country")
c1, c2 = st.columns(2)
sel_year = c1.selectbox("Year:", years, index=len(years) - 1,
                        format_func=lambda y: year_label(y, prelim_years))
as_share = c2.radio("Show as:", ["TWh", "% of total"], horizontal=True) == "% of total"

yr = (sectors[sectors["Year"] == sel_year].set_index("Country")
      .reindex(COUNTRY_COLS).dropna(how="all"))
unit = "%" if as_share else "TWh"
plot_y = yr[SECTOR_COLS].div(yr["Total"], axis=0) * 100 if as_share else yr[SECTOR_COLS]
fig = stacked_bars(plot_y, unit, ".1f")
fig.update_yaxes(title_text=f"{year_label(sel_year, prelim_years)} ({unit})",
                 tickformat=".0f")
st.plotly_chart(fig, use_container_width=True)
prelim_note(sel_year)

st.markdown("---")

# --- One country, all years
st.subheader("Development over time")
sel_country = st.selectbox("Country:", COUNTRY_COLS)
hist = sectors[sectors["Country"] == sel_country].set_index("Year").sort_index()
c_prelim = set(hist.index[hist["Source"] != EUROSTAT])
hist.index = [year_label(y, c_prelim) for y in hist.index]
fig_h = stacked_bars(hist[SECTOR_COLS], "TWh", ".2f")
fig_h.update_traces(texttemplate=None)
fig_h.update_yaxes(title_text=f"{sel_country} (TWh / year)", tickformat=".1f")
st.plotly_chart(fig_h, use_container_width=True)
if c_prelim:
    src = hist["Source"].iloc[-1].replace(" (preliminary)", "")
    st.caption(f"\\* Preliminary national data from {src}, not yet in Eurostat; "
               "may be revised.")

st.markdown("---")

# --- Table
st.subheader("Table (TWh)")
table = sectors.copy()
table["Country"] = pd.Categorical(table["Country"], COUNTRY_COLS, ordered=True)
table = table.sort_values(["Country", "Year"])
table["Year"] = [year_label(y, set(prelim.loc[prelim["Country"] == c, "Year"]))
                 for y, c in zip(table["Year"], table["Country"])]
table["Country"] = table["Country"].astype(str)
table = table.set_index(["Country", "Year"])[SECTOR_COLS + ["Total", "Source"]]
num_cols = SECTOR_COLS + ["Total"]
st.dataframe(table.style.format("{:.2f}", subset=num_cols), use_container_width=True)
st.download_button(
    "Download CSV 📥",
    table.round({c: 3 for c in num_cols}).to_csv().encode("utf-8"),
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
- Eurostat publishes the balances with about a one-year lag (first release in
  December of the following year).

**Preliminary years (marked \\*):** where Eurostat does not yet have a year, it is
taken from the national energy balance (TJ, converted to TWh) and replaced by
Eurostat automatically once Eurostat publishes it.

- **Latvia:** CSP `ENB060`, natural gas. Households = `HH`; Industry = `FC_IND_CON`
  (industry and construction); Electricity & heat = transformation input (`T`);
  Total = gross energy consumption (`GEC`). Non-energy use is not reported
  separately (it is zero in Eurostat for Latvia).
- **Lithuania:** Statistics Lithuania `S1R099_M8020305`, natural gas (`2000`).
  Households = `nu`; Industry = `pramone` + `statyba`; Non-energy use =
  `neenergetika`; Electricity & heat = public and autoproducer CHP and heat plants;
  Total = gross consumption (`bendrsunaudojimas`).
- **Finland and Estonia:** no preliminary national sector data before Eurostat.
  Statistics Finland publishes its full energy balance in December and Statistics
  Estonia in February, i.e. at the same time as Eurostat or later.
- For 2024, where both exist, the national figures differ from Eurostat by about
  1-2 % per sector.
- Energy units are on a net calorific value basis, so totals are roughly 10 % lower
  than gross calorific value figures such as ENTSOG flows.
        """
    )
