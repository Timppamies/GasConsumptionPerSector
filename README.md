# FinBalt Gas Consumption by Sector

Streamlit app showing yearly natural gas consumption in Finland, Estonia, Latvia and
Lithuania by sector: households, industry, non-energy use (feedstock), electricity &
heat, and other uses.

Data: Eurostat complete energy balances (`nrg_bal_c`), fetched live from the Eurostat API.
Years that Eurostat has not published yet are filled with preliminary national energy
balances and marked with `*`:

- Latvia: CSP Latvia `ENB060` (PxWeb API)
- Lithuania: Statistics Lithuania `S1R099_M8020305` (SDMX API)

Finland and Estonia publish their national balances at the same time as Eurostat or
later, so they have no preliminary source.

```
pip install -r requirements.txt
streamlit run app.py
```
