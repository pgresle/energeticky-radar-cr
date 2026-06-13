import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from io import StringIO

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(
    page_title="Energetický radar ČR",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>
.block-container {padding-top: 1rem; padding-bottom: 2rem; max-width: 1150px;}
.big-title {font-size: 2.1rem; font-weight: 800; line-height: 1.1; margin-bottom: 0.3rem;}
.subtitle {font-size: 1rem; color: #555; margin-bottom: 1rem;}
@media (max-width: 700px) {
    .big-title {font-size: 1.55rem;}
    .subtitle {font-size: 0.95rem;}
    .block-container {padding-left: 0.8rem; padding-right: 0.8rem;}
    div[data-testid="stMetric"] {background-color: #f8f9fb; padding: 0.7rem; border-radius: 14px; border: 1px solid #e5e7eb;}
}
</style>
""", unsafe_allow_html=True)

OTE_DAM_URL = "https://www.ote-cr.cz/cs/kratkodobe-trhy/elektrina/denni-trh"
ENTSOE_API_URL = "https://web-api.tp.entsoe.eu/api"
CZ_DOMAIN = "10YCZ-CEPS-----N"

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

PSR_TYPE_MAP = {
    "B01": "Biomasa", "B02": "Hnědé uhlí / lignit", "B03": "Uhelný plyn", "B04": "Plyn",
    "B05": "Černé uhlí", "B06": "Ropa", "B07": "Ropné břidlice", "B08": "Rašelina",
    "B09": "Geotermální energie", "B10": "Přečerpávací vodní elektrárny",
    "B11": "Průtočné vodní elektrárny", "B12": "Akumulační vodní elektrárny",
    "B13": "Mořská energie", "B14": "Jádro", "B15": "Ostatní OZE", "B16": "Fotovoltaika",
    "B17": "Odpad", "B18": "Vítr offshore", "B19": "Vítr onshore", "B20": "Ostatní",
    "B25": "Akumulace energie",
}

def parse_czech_number(value):
    if pd.isna(value):
        return None
    value = str(value).replace("\xa0", " ").replace(" ", "").replace(",", ".")
    value = re.sub(r"[^0-9.\-]", "", value)
    if value in ["", "-", ".", "-."]:
        return None
    try:
        return float(value)
    except ValueError:
        return None

def flatten_columns(columns):
    if isinstance(columns, pd.MultiIndex):
        out = []
        for col in columns:
            parts = [str(x).strip() for x in col if str(x).strip() and not str(x).startswith("Unnamed")]
            out.append(" ".join(parts))
        return out
    return [str(c).strip() for c in columns]

def strip_xml_namespace(tag):
    return tag.split("}", 1)[-1] if "}" in tag else tag

def parse_resolution(resolution):
    if resolution == "PT15M":
        return timedelta(minutes=15)
    if resolution == "PT30M":
        return timedelta(minutes=30)
    if resolution in ["PT60M", "PT1H"]:
        return timedelta(hours=1)
    return timedelta(hours=1)

def to_entsoe_time(dt):
    return dt.strftime("%Y%m%d%H%M")

@st.cache_data(ttl=1800)
def get_open_meteo(lat: float, lon: float) -> pd.DataFrame:
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join([
            "temperature_2m", "cloud_cover", "wind_speed_10m", "wind_gusts_10m",
            "shortwave_radiation", "direct_radiation", "diffuse_radiation"
        ]),
        "forecast_days": 3,
        "timezone": "Europe/Prague"
    }
    response = requests.get(url, params=params, timeout=20)
    response.raise_for_status()
    df = pd.DataFrame(response.json()["hourly"])
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
        cols = " | ".join(table.columns)
        if "Časový interval" in cols and "15min cena" in cols:
            price_table = table
            break
    if price_table is None:
        raise ValueError("Na stránce OTE se nepodařilo najít tabulku s 15minutovými cenami.")
    interval_col = price_col = volume_col = export_col = import_col = balance_col = None
    for col in price_table.columns:
        s = str(col)
        if "Časový interval" in s:
            interval_col = col
        if "15min cena" in s and "EUR" in s:
            price_col = col
        if "Množství" in s and "MWh" in s and volume_col is None:
            volume_col = col
        if "Export" in s:
            export_col = col
        if "Import" in s:
            import_col = col
        if "Saldo" in s:
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
    df["time"] = pd.to_datetime(delivery_date.strftime("%Y-%m-%d") + " " + start_times, format="%Y-%m-%d %H:%M", errors="coerce")
    df = df.dropna(subset=["time"]).sort_values("time")
    return df, delivery_date_text

@st.cache_data(ttl=1800)
def get_entsoe_generation_by_type(token: str, hours_back: int = 48) -> pd.DataFrame:
    if not token:
        raise ValueError("Chybí ENTSO-E token.")
    end_utc = datetime.now(timezone.utc)
    start_utc = end_utc - timedelta(hours=hours_back)
    params = {
        "securityToken": token,
        "documentType": "A75",
        "processType": "A16",
        "in_Domain": CZ_DOMAIN,
        "periodStart": to_entsoe_time(start_utc),
        "periodEnd": to_entsoe_time(end_utc),
    }
    response = requests.get(ENTSOE_API_URL, params=params, timeout=45)
    response.raise_for_status()
    xml_text = response.text
    if "No matching data found" in xml_text:
        raise ValueError("ENTSO-E nevrátilo žádná data pro zvolený interval.")
    root = ET.fromstring(xml_text)
    rows = []
    for ts in root.iter():
        if strip_xml_namespace(ts.tag) != "TimeSeries":
            continue
        psr_code = None
        for child in ts.iter():
            if strip_xml_namespace(child.tag) == "psrType":
                psr_code = child.text
                break
        source_name = PSR_TYPE_MAP.get(psr_code, psr_code or "Neznámý zdroj")
        for period in ts.iter():
            if strip_xml_namespace(period.tag) != "Period":
                continue
            start_text = resolution_text = None
            for child in period.iter():
                local_tag = strip_xml_namespace(child.tag)
                if local_tag == "start":
                    start_text = child.text
                if local_tag == "resolution":
                    resolution_text = child.text
            if not start_text:
                continue
            period_start_utc = pd.to_datetime(start_text, utc=True)
            step = parse_resolution(resolution_text)
            for point in period.iter():
                if strip_xml_namespace(point.tag) != "Point":
                    continue
                position = quantity = None
                for child in point:
                    local_tag = strip_xml_namespace(child.tag)
                    if local_tag == "position":
                        position = int(child.text)
                    if local_tag == "quantity":
                        quantity = float(child.text)
                if position is None or quantity is None:
                    continue
                timestamp_utc = period_start_utc + (position - 1) * step
                timestamp_prague = timestamp_utc.tz_convert("Europe/Prague").tz_localize(None)
                rows.append({"time": timestamp_prague, "source": source_name, "mw": quantity})
    df = pd.DataFrame(rows)
    if df.empty:
        raise ValueError("ENTSO-E data byla stažena, ale nepodařilo se je rozparsovat.")
    return df.sort_values("time")

st.markdown('<div class="big-title">⚡ Energetický radar ČR</div>', unsafe_allow_html=True)
st.markdown("""
<div class="subtitle">
Mobilní dashboard pro Česko: počasí pro obnovitelné zdroje,
ceny denního trhu OTE a výroba elektřiny po zdrojích z ENTSO-E.
</div>
""", unsafe_allow_html=True)
st.divider()

with st.sidebar:
    st.header("Nastavení")
    location_name = st.selectbox("Lokalita pro počasí", list(LOCATIONS.keys()), index=2)
    weather_days = st.slider("Počasí – počet dní", min_value=1, max_value=3, value=2)
    generation_hours = st.slider("Výroba ENTSO-E – kolik hodin zpět", min_value=12, max_value=72, value=48, step=12)
    st.markdown("---")
    st.markdown("### ENTSO-E token")
    token_from_secrets = ""
    try:
        token_from_secrets = st.secrets.get("ENTSOE_TOKEN", "")
    except Exception:
        token_from_secrets = ""
    token_from_input = st.text_input("ENTSO-E security token", value="", type="password", help="Na Streamlit Cloud je lepší uložit token do Secrets jako ENTSOE_TOKEN.")
    entsoe_token = token_from_secrets or token_from_input

location = LOCATIONS[location_name]
weather_error = price_error = generation_error = None
weather = pd.DataFrame()
prices = pd.DataFrame()
generation = pd.DataFrame()
ote_delivery_date = ""

try:
    weather = get_open_meteo(location["lat"], location["lon"])
except Exception as e:
    weather_error = str(e)
try:
    prices, ote_delivery_date = get_ote_day_ahead_prices()
except Exception as e:
    price_error = str(e)
try:
    if entsoe_token:
        generation = get_entsoe_generation_by_type(token=entsoe_token, hours_back=generation_hours)
    else:
        generation_error = "Pro zobrazení výroby po zdrojích vlož ENTSO-E token."
except Exception as e:
    generation_error = str(e)

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
    metric_cols[2].metric(f"OTE průměr {ote_delivery_date}", f"{prices['price_eur_mwh'].mean():.2f} EUR/MWh")
    metric_cols[3].metric("Min / max cena", f"{prices['price_eur_mwh'].min():.2f} / {prices['price_eur_mwh'].max():.2f}")
else:
    metric_cols[2].metric("OTE cena", "N/A")
    metric_cols[3].metric("Min / max cena", "N/A")

if weather_error:
    st.error(f"Počasí se nepodařilo načíst: {weather_error}")
if price_error:
    st.error(f"Ceny OTE se nepodařilo načíst: {price_error}")
if not weather.empty:
    st.info(make_renewables_comment(now_row))

tab1, tab2, tab3 = st.tabs(["🌤️ Vítr a slunce", "💶 Cena elektřiny OTE", "⚙️ Výroba elektřiny ENTSO-E"])

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
        fig_sun = px.line(weather_view, x="time", y=["shortwave_radiation", "direct_radiation", "diffuse_radiation"], labels={"time": "čas", "value": "W/m²", "variable": "typ záření"}, title="Sluneční záření")
        fig_sun.update_layout(legend_title_text="Typ záření", margin=dict(l=10, r=10, t=60, b=10))
        st.plotly_chart(fig_sun, use_container_width=True)
        fig_wind = px.line(weather_view, x="time", y=["wind_speed_10m", "wind_gusts_10m"], labels={"time": "čas", "value": "km/h", "variable": "ukazatel"}, title="Vítr a nárazy větru")
        fig_wind.update_layout(legend_title_text="Ukazatel", margin=dict(l=10, r=10, t=60, b=10))
        st.plotly_chart(fig_wind, use_container_width=True)
        fig_cloud = px.area(weather_view, x="time", y="cloud_cover", labels={"time": "čas", "cloud_cover": "oblačnost (%)"}, title="Oblačnost")
        fig_cloud.update_layout(margin=dict(l=10, r=10, t=60, b=10))
        st.plotly_chart(fig_cloud, use_container_width=True)

with tab2:
    st.subheader("Cena elektřiny – denní trh OTE")
    if prices.empty:
        st.error("Ceny OTE nejsou dostupné.")
    else:
        st.caption(f"Datum dodávky podle stránky OTE: {ote_delivery_date}")
        fig_price = px.line(prices, x="time", y="price_eur_mwh", labels={"time": "čas", "price_eur_mwh": "EUR/MWh"}, title="15minutové ceny denního trhu OTE")
        fig_price.update_layout(margin=dict(l=10, r=10, t=60, b=10))
        st.plotly_chart(fig_price, use_container_width=True)
        cheapest = prices.loc[prices["price_eur_mwh"].idxmin()]
        most_expensive = prices.loc[prices["price_eur_mwh"].idxmax()]
        c1, c2, c3 = st.columns(3)
        c1.metric("Průměr", f"{prices['price_eur_mwh'].mean():.2f} EUR/MWh")
        c2.metric("Nejlevnější interval", cheapest["time"].strftime("%H:%M"), f"{cheapest['price_eur_mwh']:.2f} EUR/MWh")
        c3.metric("Nejdražší interval", most_expensive["time"].strftime("%H:%M"), f"{most_expensive['price_eur_mwh']:.2f} EUR/MWh")
        negative_count = int((prices["price_eur_mwh"] < 0).sum())
        if negative_count > 0:
            st.warning(f"Počet intervalů se zápornou cenou: {negative_count}")
        else:
            st.success("V načtených datech nejsou záporné ceny.")
        shown_cols = [col for col in ["time", "interval", "price_eur_mwh", "volume_mwh", "export_mwh", "import_mwh", "balance_mwh"] if col in prices.columns]
        st.dataframe(prices[shown_cols], use_container_width=True, hide_index=True)

with tab3:
    st.subheader("Výroba elektřiny po zdrojích – ENTSO-E")
    if generation_error:
        st.error(generation_error)
        st.info("Token můžeš vložit vlevo v postranním panelu nebo do Streamlit Secrets jako ENTSOE_TOKEN.")
    elif generation.empty:
        st.error("Výroba z ENTSO-E není dostupná.")
    else:
        latest_time = generation["time"].max()
        latest = generation[generation["time"] == latest_time].copy()
        latest_total = latest["mw"].sum()
        st.caption(f"Poslední dostupný interval ENTSO-E: {latest_time.strftime('%d.%m.%Y %H:%M')}")
        c1, c2 = st.columns(2)
        c1.metric("Celková výroba v posledním intervalu", f"{latest_total:.0f} MW")
        main_sources = latest.sort_values("mw", ascending=False).head(3)
        top_text = ", ".join(f"{row['source']} {row['mw']:.0f} MW" for _, row in main_sources.iterrows())
        c2.metric("Největší zdroje", top_text)
        pivot = generation.pivot_table(index="time", columns="source", values="mw", aggfunc="sum").fillna(0)
        pivot_long = pivot.reset_index().melt(id_vars="time", var_name="source", value_name="mw")
        fig_area = px.area(pivot_long, x="time", y="mw", color="source", labels={"time": "čas", "mw": "MW", "source": "zdroj"}, title="Výroba elektřiny po zdrojích")
        fig_area.update_layout(legend_title_text="Zdroj", margin=dict(l=10, r=10, t=60, b=10))
        st.plotly_chart(fig_area, use_container_width=True)
        fig_latest = px.bar(latest.sort_values("mw", ascending=False), x="source", y="mw", labels={"source": "zdroj", "mw": "MW"}, title="Poslední dostupný mix výroby")
        fig_latest.update_layout(xaxis_tickangle=-25, margin=dict(l=10, r=10, t=60, b=10))
        st.plotly_chart(fig_latest, use_container_width=True)
        st.dataframe(latest.sort_values("mw", ascending=False), use_container_width=True, hide_index=True)

st.divider()
st.caption("Energetický radar ČR | data: Open-Meteo + OTE + ENTSO-E")
