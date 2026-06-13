import streamlit as st
import pandas as pd
import requests
import plotly.express as px
from datetime import datetime, timedelta

st.set_page_config(
    page_title="Energetický radar ČR",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# -------------------------------
# Mobilně přívětivé CSS
# -------------------------------
st.markdown("""
<style>
.block-container {
    padding-top: 1rem;
    padding-bottom: 2rem;
    max-width: 1100px;
}
.metric-card {
    background: #f6f7f9;
    border-radius: 18px;
    padding: 16px 18px;
    margin-bottom: 10px;
    border: 1px solid #e6e8eb;
}
.big-title {
    font-size: 2.1rem;
    font-weight: 800;
    line-height: 1.1;
}
.subtitle {
    font-size: 1rem;
    color: #555;
}
.small-note {
    font-size: 0.85rem;
    color: #666;
}
@media (max-width: 700px) {
    .big-title {
        font-size: 1.55rem;
    }
    .block-container {
        padding-left: 0.8rem;
        padding-right: 0.8rem;
    }
}
</style>
""", unsafe_allow_html=True)


# -------------------------------
# Lokality
# -------------------------------
LOCATIONS = {
    "Praha": {"lat": 50.0755, "lon": 14.4378},
    "Brno": {"lat": 49.1951, "lon": 16.6068},
    "Ostrava": {"lat": 49.8209, "lon": 18.2625},
    "Plzeň": {"lat": 49.7384, "lon": 13.3736},
    "Ústí nad Labem": {"lat": 50.6611, "lon": 14.0327},
    "Jihlava / Vysočina": {"lat": 49.3961, "lon": 15.5912},
    "Krušné hory – Klínovec": {"lat": 50.3964, "lon": 12.9674},
    "Jeseníky – Praděd": {"lat": 50.0833, "lon": 17.2333},
}


@st.cache_data(ttl=1800)
def get_open_meteo(lat: float, lon: float) -> pd.DataFrame:
    """
    Free Open-Meteo forecast API.
    No API key needed for basic non-commercial use.
    """
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join([
            "temperature_2m",
            "cloud_cover",
            "wind_speed_10m",
            "wind_gusts_10m",
            "shortwave_radiation",
            "direct_radiation",
            "diffuse_radiation"
        ]),
        "forecast_days": 3,
        "timezone": "Europe/Prague"
    }
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()["hourly"]
    df = pd.DataFrame(data)
    df["time"] = pd.to_datetime(df["time"])
    return df


def make_renewables_comment(now_row: pd.Series) -> str:
    wind = now_row.get("wind_speed_10m", 0)
    radiation = now_row.get("shortwave_radiation", 0)
    cloud = now_row.get("cloud_cover", 100)

    solar_score = min(100, max(0, radiation / 8))
    wind_score = min(100, max(0, wind * 10))

    if solar_score > 65 and wind_score > 45:
        return "Dnes to vypadá dobře pro fotovoltaiku i vítr."
    if solar_score > 65:
        return "Dnes jsou dobré podmínky hlavně pro fotovoltaiku."
    if wind_score > 55:
        return "Dnes jsou lepší podmínky hlavně pro vítr."
    if cloud > 80 and wind < 4:
        return "Dnes počasí obnovitelným zdrojům moc nepomáhá."
    return "Podmínky pro OZE jsou dnes spíše střední."


def synthetic_price_data(weather_df: pd.DataFrame) -> pd.DataFrame:
    """
    Dočasná demonstrační cena, než se napojí OTE/ENTSO-E.
    Není to reálná tržní cena.
    """
    df = weather_df[["time", "shortwave_radiation", "wind_speed_10m"]].copy()
    hour = df["time"].dt.hour

    # základní denní profil: levněji v noci a kolem poledne, dražší ráno/večer
    base = 95
    evening_peak = ((hour >= 17) & (hour <= 21)).astype(int) * 35
    morning_peak = ((hour >= 7) & (hour <= 9)).astype(int) * 20
    solar_discount = (df["shortwave_radiation"] / 1000) * 35
    wind_discount = df["wind_speed_10m"] * 2.0

    df["price_eur_mwh_demo"] = base + evening_peak + morning_peak - solar_discount - wind_discount
    df["price_eur_mwh_demo"] = df["price_eur_mwh_demo"].round(1)
    return df


# -------------------------------
# Header
# -------------------------------
st.markdown('<div class="big-title">⚡ Energetický radar ČR</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="subtitle">Mobilní dashboard: počasí pro OZE, orientační cenový profil a příprava na data OTE / ENTSO‑E / ČEPS.</div>',
    unsafe_allow_html=True
)

st.divider()

# -------------------------------
# Ovládání
# -------------------------------
col_a, col_b = st.columns([2, 1])
with col_a:
    location_name = st.selectbox("Vyber lokalitu", list(LOCATIONS.keys()), index=2)
with col_b:
    view_days = st.slider("Počet dní", min_value=1, max_value=3, value=2)

loc = LOCATIONS[location_name]

try:
    weather = get_open_meteo(loc["lat"], loc["lon"])
except Exception as e:
    st.error(f"Nepodařilo se načíst data z Open‑Meteo: {e}")
    st.stop()

end_time = weather["time"].min() + timedelta(days=view_days)
weather_view = weather[weather["time"] < end_time].copy()

now = pd.Timestamp.now(tz="Europe/Prague").tz_localize(None)
weather_view["abs_diff_now"] = (weather_view["time"] - now).abs()
now_row = weather_view.loc[weather_view["abs_diff_now"].idxmin()]

price_demo = synthetic_price_data(weather_view)

# -------------------------------
# Top metriky
# -------------------------------
st.subheader("Dnes v OZE počasí")

m1, m2, m3, m4 = st.columns(4)
m1.metric("Vítr", f"{now_row['wind_speed_10m']:.1f} km/h")
m2.metric("Nárazy", f"{now_row['wind_gusts_10m']:.1f} km/h")
m3.metric("Sluneční záření", f"{now_row['shortwave_radiation']:.0f} W/m²")
m4.metric("Oblačnost", f"{now_row['cloud_cover']:.0f} %")

st.info(make_renewables_comment(now_row))

# -------------------------------
# Tabs
# -------------------------------
tab1, tab2, tab3, tab4 = st.tabs([
    "🌤️ Vítr a slunce",
    "💶 Cena elektřiny",
    "⚙️ Výroba elektřiny",
    "ℹ️ Zdroje a další kroky"
])

with tab1:
    st.subheader(f"Počasí pro OZE: {location_name}")

    fig_sun = px.line(
        weather_view,
        x="time",
        y=["shortwave_radiation", "direct_radiation", "diffuse_radiation"],
        labels={
            "time": "čas",
            "value": "W/m²",
            "variable": "typ záření"
        },
        title="Sluneční záření"
    )
    st.plotly_chart(fig_sun, use_container_width=True)

    fig_wind = px.line(
        weather_view,
        x="time",
        y=["wind_speed_10m", "wind_gusts_10m"],
        labels={
            "time": "čas",
            "value": "km/h",
            "variable": "ukazatel"
        },
        title="Vítr"
    )
    st.plotly_chart(fig_wind, use_container_width=True)

    fig_cloud = px.area(
        weather_view,
        x="time",
        y="cloud_cover",
        labels={"time": "čas", "cloud_cover": "oblačnost (%)"},
        title="Oblačnost"
    )
    st.plotly_chart(fig_cloud, use_container_width=True)


with tab2:
    st.subheader("Cena elektřiny")

    st.warning(
        "Toto je zatím demonstrační cenový profil podle počasí, ne reálná cena OTE. "
        "Slouží k odladění vzhledu dashboardu. Reálná data OTE/ENTSO‑E se doplní v další verzi."
    )

    fig_price = px.line(
        price_demo,
        x="time",
        y="price_eur_mwh_demo",
        labels={"time": "čas", "price_eur_mwh_demo": "EUR/MWh"},
        title="Demonstrační profil ceny elektřiny"
    )
    st.plotly_chart(fig_price, use_container_width=True)

    cheapest = price_demo.loc[price_demo["price_eur_mwh_demo"].idxmin()]
    most_expensive = price_demo.loc[price_demo["price_eur_mwh_demo"].idxmax()]

    c1, c2 = st.columns(2)
    c1.metric("Nejlevnější interval", cheapest["time"].strftime("%d.%m. %H:%M"), f"{cheapest['price_eur_mwh_demo']:.1f} EUR/MWh")
    c2.metric("Nejdražší interval", most_expensive["time"].strftime("%d.%m. %H:%M"), f"{most_expensive['price_eur_mwh_demo']:.1f} EUR/MWh")


with tab3:
    st.subheader("Výroba elektřiny po zdrojích")

    st.info(
        "Sem patří napojení na ENTSO‑E / ČEPS: výroba z jádra, uhlí, plynu, vody, větru, FVE, biomasy, "
        "spotřeba a přeshraniční toky. Kostra aplikace je na to připravená."
    )

    demo_generation = pd.DataFrame({
        "Zdroj": ["Jádro", "Uhlí", "Plyn", "Voda", "Fotovoltaika", "Vítr", "Biomasa / ostatní"],
        "Výroba_GW_demo": [3.7, 2.4, 0.8, 0.5, 1.2, 0.2, 0.4]
    })

    fig_gen = px.bar(
        demo_generation,
        x="Zdroj",
        y="Výroba_GW_demo",
        title="Demonstrační mix výroby elektřiny",
        labels={"Výroba_GW_demo": "GW"}
    )
    st.plotly_chart(fig_gen, use_container_width=True)

    st.dataframe(demo_generation, use_container_width=True, hide_index=True)


with tab4:
    st.subheader("Zdroje dat a plán napojení")

    st.markdown("""
**Aktuálně funkční v této kostře**
- Open‑Meteo: vítr, nárazy, oblačnost, teplota, sluneční záření.
- Mobilní layout pro Streamlit.
- Demonstrační cenový profil.

**K doplnění v další verzi**
- OTE: reálné ceny denního trhu.
- ENTSO‑E: výroba po zdrojích, zatížení, přeshraniční toky.
- ČEPS: doplňkové údaje o síti.
- Mapový pohled: kraje / vybrané lokality.
- Export grafů do PNG/CSV.

**Poznámka**
Demonstrační data o ceně a výrobě nejsou reálná tržní ani provozní data.
Mají jen ukázat, jak bude aplikace vypadat.
""")

st.divider()
st.caption("Pracovní verze dashboardu: Energetický radar ČR | Python + Streamlit + Plotly + Open‑Meteo")