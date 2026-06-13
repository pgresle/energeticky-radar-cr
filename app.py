import re
from datetime import timedelta

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
import streamlit.components.v1 as components
from bs4 import BeautifulSoup
from io import StringIO


# ============================================================
# NASTAVENÍ STRÁNKY
# ============================================================

st.set_page_config(
    page_title="Energetický radar ČR",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed"
)


# ============================================================
# CSS
# ============================================================

st.markdown("""
<style>
.block-container {
    padding-top: 3.5rem;
    padding-bottom: 2rem;
    max-width: 1150px;
}

.big-title {
    font-size: 2.1rem;
    font-weight: 800;
    line-height: 1.15;
    margin-top: 0.4rem;
    margin-bottom: 0.5rem;
}

.subtitle {
    font-size: 1rem;
    color: #555;
    margin-bottom: 1rem;
}

div[data-testid="stMetricValue"] {
    font-size: 2rem;
    line-height: 1.1;
}

@media (max-width: 700px) {
    .block-container {
        padding-top: 4.2rem;
        padding-left: 0.8rem;
        padding-right: 0.8rem;
    }

    .big-title {
        font-size: 1.55rem;
        line-height: 1.2;
        margin-top: 0.7rem;
    }

    .subtitle {
        font-size: 0.95rem;
    }

    div[data-testid="stMetric"] {
        background-color: #f8f9fb;
        padding: 0.7rem;
        border-radius: 14px;
        border: 1px solid #e5e7eb;
    }

    div[data-testid="stMetricValue"] {
        font-size: 1.55rem;
        line-height: 1.15;
    }
}
</style>
""", unsafe_allow_html=True)

# ============================================================
# KONSTANTY
# ============================================================

OTE_DAM_URL = "https://www.ote-cr.cz/cs/kratkodobe-trhy/elektrina/denni-trh"

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


# ============================================================
# POMOCNÉ FUNKCE
# ============================================================

def parse_czech_number(value):
    if pd.isna(value):
        return None

    value = str(value)
    value = value.replace("\xa0", " ")
    value = value.replace(" ", "")
    value = value.replace(",", ".")
    value = re.sub(r"[^0-9.\-]", "", value)

    if value in ["", "-", ".", "-."]:
        return None

    try:
        return float(value)
    except ValueError:
        return None


def flatten_columns(columns):
    if isinstance(columns, pd.MultiIndex):
        result = []
        for col in columns:
            parts = [
                str(x).strip()
                for x in col
                if str(x).strip() and not str(x).startswith("Unnamed")
            ]
            result.append(" ".join(parts))
        return result

    return [str(c).strip() for c in columns]


def make_energostat_url(chart_type: str, start_date, end_date) -> str:
    start = start_date.strftime("%Y-%m-%d")
    end = end_date.strftime("%Y-%m-%d")
    return f"https://oenergetice.cz/energostat/power/{chart_type}/czech/{start}/{end}"


# ============================================================
# OPEN-METEO: POČASÍ
# ============================================================

@st.cache_data(ttl=1800)
def get_open_meteo(lat: float, lon: float) -> pd.DataFrame:
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

    response = requests.get(url, params=params, timeout=20)
    response.raise_for_status()

    data = response.json()["hourly"]
    df = pd.DataFrame(data)
    df["time"] = pd.to_datetime(df["time"])

    return df


def make_renewables_comment(row: pd.Series) -> str:
    wind = row.get("wind_speed_10m", 0)
    radiation = row.get("shortwave_radiation", 0)
    cloud = row.get("cloud_cover", 100)

    solar_score = min(100, max(0, radiation / 8))
    wind_score = min(100, max(0, wind * 10))

    if solar_score > 65 and wind_score > 45:
        return "Aktuálně jsou dobré podmínky pro fotovoltaiku i vítr."

    if solar_score > 65:
        return "Aktuálně jsou dobré podmínky hlavně pro fotovoltaiku."

    if wind_score > 55:
        return "Aktuálně jsou lepší podmínky hlavně pro vítr."

    if cloud > 80 and wind < 4:
        return "Aktuálně počasí obnovitelným zdrojům moc nepomáhá."

    return "Aktuální podmínky pro obnovitelné zdroje jsou spíše střední."


# ============================================================
# OTE: REÁLNÉ CENY DENNÍHO TRHU
# ============================================================

@st.cache_data(ttl=900)
def get_ote_day_ahead_prices() -> tuple[pd.DataFrame, str]:
    headers = {"User-Agent": "Mozilla/5.0"}

    response = requests.get(OTE_DAM_URL, headers=headers, timeout=30)
    response.raise_for_status()

    html = response.text
    soup = BeautifulSoup(html, "html.parser")

    heading_text = ""
    for tag in soup.find_all(["h1", "h2", "h3"]):
        text = tag.get_text(" ", strip=True)
        if "Výsledky denního trhu" in text:
            heading_text = text
            break

    tables = pd.read_html(StringIO(html), decimal=",", thousands=" ")

    price_table = None

    for table in tables:
        table = table.copy()
        table.columns = flatten_columns(table.columns)
        columns_joined = " | ".join(table.columns)

        if "Časový interval" in columns_joined and "15min cena" in columns_joined:
            price_table = table
            break

    if price_table is None:
        raise ValueError("Na stránce OTE se nepodařilo najít tabulku s 15minutovými cenami.")

    interval_col = None
    price_col = None
    volume_col = None
    export_col = None
    import_col = None
    balance_col = None

    for col in price_table.columns:
        col_clean = str(col)

        if "Časový interval" in col_clean:
            interval_col = col

        if "15min cena" in col_clean and "EUR" in col_clean:
            price_col = col

        if "Množství" in col_clean and "MWh" in col_clean and volume_col is None:
            volume_col = col

        if "Export" in col_clean:
            export_col = col

        if "Import" in col_clean:
            import_col = col

        if "Saldo" in col_clean:
            balance_col = col

    if interval_col is None or price_col is None:
        raise ValueError("Tabulka OTE byla nalezena, ale nejde rozpoznat interval nebo cenu.")

    df = pd.DataFrame()

    df["interval"] = price_table[interval_col].astype(str)
    df["price_eur_mwh"] = price_table[price_col].apply(parse_czech_number)

    if volume_col is not None:
        df["volume_mwh"] = price_table[volume_col].apply(parse_czech_number)

    if export_col is not None:
        df["export_mwh"] = price_table[export_col].apply(parse_czech_number)

    if import_col is not None:
        df["import_mwh"] = price_table[import_col].apply(parse_czech_number)

    if balance_col is not None:
        df["balance_mwh"] = price_table[balance_col].apply(parse_czech_number)

    df = df.dropna(subset=["price_eur_mwh"])
    df = df[df["interval"].str.contains("-", regex=False)]

    date_match = re.search(r"(\d{2}\.\d{2}\.\d{4})", heading_text)

    if date_match:
        delivery_date = pd.to_datetime(date_match.group(1), format="%d.%m.%Y")
        delivery_date_text = date_match.group(1)
    else:
        delivery_date = pd.Timestamp.now(tz="Europe/Prague").tz_localize(None).normalize()
        delivery_date_text = delivery_date.strftime("%d.%m.%Y")

    start_times = df["interval"].str.split("-", expand=True)[0]
    df["time"] = pd.to_datetime(
        delivery_date.strftime("%Y-%m-%d") + " " + start_times,
        format="%Y-%m-%d %H:%M",
        errors="coerce"
    )

    df = df.dropna(subset=["time"])
    df = df.sort_values("time")

    return df, delivery_date_text


# ============================================================
# HLAVIČKA
# ============================================================

st.markdown(
    '<div class="big-title">⚡ Energetický radar ČR</div>',
    unsafe_allow_html=True
)

st.markdown(
    """
    <div class="subtitle">
    Mobilní dashboard pro Česko: počasí pro obnovitelné zdroje,
    ceny denního trhu OTE a výroba elektřiny přes Energostat.
    </div>
    """,
    unsafe_allow_html=True
)

st.divider()


# ============================================================
# SIDEBAR
# ============================================================

st.subheader("Nastavení dashboardu")

control_col1, control_col2, control_col3 = st.columns([2, 1, 2])

with control_col1:
    location_name = st.selectbox(
        "Lokalita pro počasí",
        list(LOCATIONS.keys()),
        index=2
    )

with control_col2:
    weather_days = st.slider(
        "Počasí – dní",
        min_value=1,
        max_value=3,
        value=2
    )

with control_col3:
    energostat_chart = st.selectbox(
        "Energostat graf",
        {
            "generation-online": "Výroba elektřiny",
            "generation-share": "Podíl zdrojů na výrobě",
            "load": "Zatížení soustavy",
            "day-prices": "Spotové ceny"
        },
        format_func=lambda x: {
            "generation-online": "Výroba elektřiny",
            "generation-share": "Podíl zdrojů na výrobě",
            "load": "Zatížení soustavy",
            "day-prices": "Spotové ceny"
        }[x]
    )

energostat_days = st.slider(
    "Energostat – počet dní zpět",
    min_value=1,
    max_value=30,
    value=7
)

# ============================================================
# NAČTENÍ DAT
# ============================================================

location = LOCATIONS[location_name]

weather_error = None
price_error = None

weather = pd.DataFrame()
prices = pd.DataFrame()
ote_delivery_date = ""

try:
    weather = get_open_meteo(location["lat"], location["lon"])
except Exception as e:
    weather_error = str(e)

try:
    prices, ote_delivery_date = get_ote_day_ahead_prices()
except Exception as e:
    price_error = str(e)


# ============================================================
# TOP KARTY
# ============================================================

st.subheader("Aktuální přehled")

metric_cols = st.columns(4)

if not weather.empty:
    end_time = weather["time"].min() + timedelta(days=weather_days)
    weather_view = weather[weather["time"] < end_time].copy()

    now = pd.Timestamp.now(tz="Europe/Prague").tz_localize(None)
    weather_view["abs_diff_now"] = (weather_view["time"] - now).abs()
    now_row = weather_view.loc[weather_view["abs_diff_now"].idxmin()]

    metric_cols[0].metric("Vítr", f"{now_row['wind_speed_10m']:.1f} km/h")
    metric_cols[1].metric("Slunce", f"{now_row['shortwave_radiation']:.0f} W/m²")
else:
    weather_view = pd.DataFrame()
    now_row = pd.Series()
    metric_cols[0].metric("Vítr", "N/A")
    metric_cols[1].metric("Slunce", "N/A")

if not prices.empty:
    avg_price = prices["price_eur_mwh"].mean()
    min_price = prices["price_eur_mwh"].min()
    max_price = prices["price_eur_mwh"].max()

    metric_cols[2].metric(
        f"OTE průměr {ote_delivery_date}",
        f"{avg_price:.2f} EUR/MWh"
    )

    metric_cols[3].metric(
        "Min / max cena",
        f"{min_price:.2f} / {max_price:.2f}"
    )
else:
    metric_cols[2].metric("OTE cena", "N/A")
    metric_cols[3].metric("Min / max cena", "N/A")

if weather_error:
    st.error(f"Počasí se nepodařilo načíst: {weather_error}")

if price_error:
    st.error(f"Ceny OTE se nepodařilo načíst: {price_error}")

if not weather.empty:
    st.info(make_renewables_comment(now_row))


# ============================================================
# TABS
# ============================================================

tab1, tab2, tab3 = st.tabs([
    "🌤️ Vítr a slunce",
    "💶 Cena elektřiny OTE",
    "⚙️ Výroba a síť Energostat"
])


# ============================================================
# TAB 1
# ============================================================

with tab1:
    st.subheader(f"Počasí pro OZE: {location_name}")

    if weather.empty:
        st.error("Počasí není dostupné.")
    else:
        c1, c2, c3, c4 = st.columns(4)

        c1.metric("Teplota", f"{now_row['temperature_2m']:.1f} °C")
        c2.metric("Vítr", f"{now_row['wind_speed_10m']:.1f} km/h")
        c3.metric("Nárazy", f"{now_row['wind_gusts_10m']:.1f} km/h")
        c4.metric("Oblačnost", f"{now_row['cloud_cover']:.0f} %")

        fig_sun = px.line(
            weather_view,
            x="time",
            y=["shortwave_radiation", "direct_radiation", "diffuse_radiation"],
            labels={"time": "čas", "value": "W/m²", "variable": "typ záření"},
            title="Sluneční záření"
        )
        fig_sun.update_layout(
            legend_title_text="Typ záření",
            margin=dict(l=10, r=10, t=60, b=10)
        )
        st.plotly_chart(fig_sun, use_container_width=True)

        fig_wind = px.line(
            weather_view,
            x="time",
            y=["wind_speed_10m", "wind_gusts_10m"],
            labels={"time": "čas", "value": "km/h", "variable": "ukazatel"},
            title="Vítr a nárazy větru"
        )
        fig_wind.update_layout(
            legend_title_text="Ukazatel",
            margin=dict(l=10, r=10, t=60, b=10)
        )
        st.plotly_chart(fig_wind, use_container_width=True)

        fig_cloud = px.area(
            weather_view,
            x="time",
            y="cloud_cover",
            labels={"time": "čas", "cloud_cover": "oblačnost (%)"},
            title="Oblačnost"
        )
        fig_cloud.update_layout(margin=dict(l=10, r=10, t=60, b=10))
        st.plotly_chart(fig_cloud, use_container_width=True)


# ============================================================
# TAB 2
# ============================================================

with tab2:
    st.subheader("Cena elektřiny – denní trh OTE")

    if prices.empty:
        st.error("Ceny OTE nejsou dostupné.")
    else:
        st.caption(f"Datum dodávky podle stránky OTE: {ote_delivery_date}")

        fig_price = px.line(
            prices,
            x="time",
            y="price_eur_mwh",
            labels={"time": "čas", "price_eur_mwh": "EUR/MWh"},
            title="15minutové ceny denního trhu OTE"
        )
        fig_price.update_layout(margin=dict(l=10, r=10, t=60, b=10))
        st.plotly_chart(fig_price, use_container_width=True)

        cheapest = prices.loc[prices["price_eur_mwh"].idxmin()]
        most_expensive = prices.loc[prices["price_eur_mwh"].idxmax()]

        c1, c2, c3 = st.columns(3)

        c1.metric("Průměr", f"{prices['price_eur_mwh'].mean():.2f} EUR/MWh")

        c2.metric(
            "Nejlevnější interval",
            cheapest["time"].strftime("%H:%M"),
            f"{cheapest['price_eur_mwh']:.2f} EUR/MWh"
        )

        c3.metric(
            "Nejdražší interval",
            most_expensive["time"].strftime("%H:%M"),
            f"{most_expensive['price_eur_mwh']:.2f} EUR/MWh"
        )

        negative_count = int((prices["price_eur_mwh"] < 0).sum())

        if negative_count > 0:
            st.warning(f"Počet intervalů se zápornou cenou: {negative_count}")
        else:
            st.success("V načtených datech nejsou záporné ceny.")

        shown_cols = [
            col for col in [
                "time",
                "interval",
                "price_eur_mwh",
                "volume_mwh",
                "export_mwh",
                "import_mwh",
                "balance_mwh"
            ]
            if col in prices.columns
        ]

        st.dataframe(
            prices[shown_cols],
            use_container_width=True,
            hide_index=True
        )


# ============================================================
# TAB 3
# ============================================================

with tab3:
    st.subheader("Výroba elektřiny a síť – Energostat")

    end_date = pd.Timestamp.now(tz="Europe/Prague").tz_localize(None).date()
    start_date = end_date - timedelta(days=energostat_days)

    energostat_url = make_energostat_url(
        energostat_chart,
        pd.Timestamp(start_date),
        pd.Timestamp(end_date)
    )

    st.markdown(
        f"""
        Data v této části jsou zobrazena přes **Energostat oEnergetice.cz**.
        Energostat uvádí jako zdroj dat ENTSO-E Transparency Platform a GIE.

        [Otevřít graf na Energostatu]({energostat_url})
        """
    )

    components.iframe(
        energostat_url,
        height=760,
        scrolling=True
    )

    st.caption(
        "Poznámka: tato meziverze vkládá veřejný graf Energostatu. "
        "Až bude k dispozici ENTSO-E token, nahradíme tuto část vlastním grafem přímo v aplikaci."
    )


st.divider()
st.caption("Energetický radar ČR | data: Open-Meteo + OTE + Energostat")
