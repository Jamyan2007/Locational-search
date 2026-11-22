
import random
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

import folium
import pycountry
import requests
import streamlit as st
from streamlit_folium import st_folium

st.set_page_config(
    page_title="Locational Search",
    layout="wide",
    initial_sidebar_state="collapsed"
)

GEO_URL = "https://www.geoboundaries.org/api/current/gbOpen"
WORLD_URL = (
    "https://raw.githubusercontent.com/"
    "datasets/geo-countries/master/data/countries.geojson"
)
TRENDS_URL = "https://trends.google.com/trending/rss"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/130.0 Safari/537.36"
    )
}

GLOBAL_COUNTRIES = [
    "US", "GB", "CA", "AU", "IN",
    "DE", "FR", "JP", "BR", "MX",
    "SG", "ZA", "NG", "KR", "MN"
]

st.markdown("""
<style>
.stApp {
    background: #080E18;
    color: #E5EDF5;
}
.block-container {
    max-width: 1900px;
    padding: 1rem 1.2rem;
}
h1, h2, h3 {
    color: #E5EDF5;
}
.story-card {
    background: #111C2B;
    border: 1px solid #26384B;
    border-radius: 8px;
    padding: 11px;
    margin-bottom: 9px;
}
.story-title {
    color: #E5EDF5;
    font-size: 14px;
    font-weight: 600;
}
.story-meta {
    color: #91A4B8;
    font-size: 11px;
    margin-top: 5px;
}
[data-testid="stButton"] button {
    background: #142438;
    color: #DCEAF5;
    border: 1px solid #30465D;
    border-radius: 6px;
}
[data-testid="stButton"] button:hover {
    background: #1B3B54;
    border-color: #4CB9D5;
    color: white;
}
[data-testid="stMetric"] {
    background: #111C2B;
    border: 1px solid #26384B;
    padding: 10px;
    border-radius: 8px;
}
</style>
""", unsafe_allow_html=True)


def request_json(url, params=None):
    response = requests.get(
        url,
        params=params,
        headers=HEADERS,
        timeout=60
    )
    response.raise_for_status()
    return response.json()


def request_xml(url, params=None):
    response = requests.get(
        url,
        params=params,
        headers=HEADERS,
        timeout=45
    )

    if response.status_code == 429:
        raise RuntimeError("Google Trends is temporarily limiting requests.")

    response.raise_for_status()
    return ET.fromstring(response.content)


def traffic_number(value):
    if not value:
        return 0

    value = value.upper().replace(",", "").replace("+", "").strip()

    match = re.search(r"([\d.]+)\s*([KMB]?)", value)

    if not match:
        return 0

    number = float(match.group(1))
    suffix = match.group(2)

    multiplier = {
        "": 1,
        "K": 1000,
        "M": 1000000,
        "B": 1000000000
    }.get(suffix, 1)

    return int(number * multiplier)


@st.cache_data(ttl=1800, show_spinner=False)
def get_trends(geo):
    root = request_xml(
        TRENDS_URL,
        {"geo": geo}
    )

    trends = []

    ns = {
        "ht": "https://trends.google.com/trending/rss"
    }

    for item in root.findall(".//item"):
        title = item.findtext("title", default="").strip()

        traffic = item.findtext(
            "ht:approx_traffic",
            default="",
            namespaces=ns
        )

        if not title:
            continue

        news_items = []

        for news in item.findall("ht:news_item", ns):
            news_title = news.findtext(
                "ht:news_item_title",
                default="",
                namespaces=ns
            )

            news_url = news.findtext(
                "ht:news_item_url",
                default="",
                namespaces=ns
            )

            if news_title:
                news_items.append({
                    "title": news_title,
                    "url": news_url
                })

        trends.append({
            "topic": title,
            "traffic": traffic,
            "traffic_number": traffic_number(traffic),
            "published": item.findtext("pubDate", default=""),
            "news": news_items,
            "geo": geo
        })

    return trends


@st.cache_data(ttl=3600, show_spinner=False)
def get_global_trends():
    collected = []

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(get_trends, country): country
            for country in GLOBAL_COUNTRIES
        }

        for future in as_completed(futures):
            country = futures[future]

            try:
                results = future.result()

                for result in results:
                    result["country"] = country
                    collected.append(result)

            except Exception:
                continue

    grouped = {}

    for trend in collected:
        key = trend["topic"].strip().casefold()

        if key not in grouped:
            grouped[key] = {
                "topic": trend["topic"],
                "countries": set(),
                "traffic": trend["traffic"],
                "traffic_number": trend["traffic_number"],
                "news": trend["news"]
            }

        grouped[key]["countries"].add(trend["country"])

        if trend["traffic_number"] > grouped[key]["traffic_number"]:
            grouped[key]["traffic"] = trend["traffic"]
            grouped[key]["traffic_number"] = trend["traffic_number"]

        if not grouped[key]["news"] and trend["news"]:
            grouped[key]["news"] = trend["news"]

    results = list(grouped.values())

    results.sort(
        key=lambda x: (
            len(x["countries"]),
            x["traffic_number"]
        ),
        reverse=True
    )

    return results[:30]


@st.cache_data(ttl=86400, show_spinner=False)
def get_world():
    return request_json(WORLD_URL)


@st.cache_data(ttl=86400, show_spinner=False)
def get_regions(country_code):
    metadata = request_json(
        f"{GEO_URL}/{country_code}/ADM1/"
    )

    geojson_url = (
        metadata.get("simplifiedGeometryGeoJSON")
        or metadata.get("gjDownloadURL")
    )

    if not geojson_url:
        raise RuntimeError("Regional map data is unavailable.")

    geojson = request_json(geojson_url)

    return metadata, geojson


def find_country(properties):
    for field in ["ISO_A3", "ADM0_A3", "ISO_A3_EH"]:
        code = properties.get(field)

        if code and code != "-99":
            country = pycountry.countries.get(alpha_3=code)

            if country:
                return country.name, country.alpha_2, country.alpha_3

    for field in ["ADMIN", "name", "NAME", "SOVEREIGNT"]:
        name = properties.get(field)

        if name:
            try:
                country = pycountry.countries.lookup(name)
                return country.name, country.alpha_2, country.alpha_3
            except LookupError:
                pass

    return None, None, None


def get_name(properties):
    for field in [
        "shapeName",
        "boundaryName",
        "name",
        "NAME_1",
        "ADMIN"
    ]:
        if properties.get(field):
            return str(properties[field])

    return None


def coordinates_average(geometry):
    points = []

    def walk(value):
        if (
            isinstance(value, list)
            and len(value) >= 2
            and isinstance(value[0], (float, int))
            and isinstance(value[1], (float, int))
        ):
            points.append((value[1], value[0]))
        elif isinstance(value, list):
            for child in value:
                walk(child)

    if geometry:
        walk(geometry.get("coordinates", []))

    if not points:
        return None

    return (
        sum(p[0] for p in points) / len(points),
        sum(p[1] for p in points) / len(points)
    )


def add_click_markers(map_object, geojson, level):
    for feature in geojson.get("features", []):
        properties = feature.get("properties", {})

        if level == "country":
            name, alpha2, alpha3 = find_country(properties)

            if not name:
                continue

            tooltip = f"{name}|{alpha2}|{alpha3}"

        else:
            name = get_name(properties)

            if not name:
                continue

            code = (
                properties.get("shapeISO")
                or properties.get("shapeISO2")
                or ""
            )

            tooltip = f"{name}|{code}"

        center = coordinates_average(feature.get("geometry"))

        if center:
            folium.CircleMarker(
                location=center,
                radius=8,
                color="#6ED9F3",
                weight=1,
                fill=True,
                fill_color="#6ED9F3",
                fill_opacity=0.01,
                opacity=0.01,
                tooltip=tooltip
            ).add_to(map_object)


def map_style(map_object):
    map_object.get_root().html.add_child(
        folium.Element("""
        <style>
        .leaflet-tile-pane {
            filter: invert(90%) hue-rotate(180deg)
                    brightness(85%) contrast(90%);
        }
        </style>
        """)
    )


def world_map(world):
    m = folium.Map(
        location=[20, 0],
        zoom_start=2,
        tiles="OpenStreetMap",
        prefer_canvas=True,
        control_scale=True
    )

    folium.GeoJson(
        world,
        style_function=lambda f: {
            "fillColor": "#193C53",
            "color": "#5BBAD8",
            "weight": 0.65,
            "fillOpacity": 0.25
        },
        highlight_function=lambda f: {
            "fillColor": "#168DB2",
            "color": "#B5EEFF",
            "weight": 2,
            "fillOpacity": 0.65
        }
    ).add_to(m)

    add_click_markers(m, world, "country")
    map_style(m)

    return m


def region_map(geojson):
    m = folium.Map(
        location=[20, 0],
        zoom_start=4,
        tiles="OpenStreetMap",
        prefer_canvas=True,
        control_scale=True
    )

    folium.GeoJson(
        geojson,
        style_function=lambda f: {
            "fillColor": "#155D83",
            "color": "#76C9E5",
            "weight": 1.2,
            "fillOpacity": 0.45
        },
        highlight_function=lambda f: {
            "fillColor": "#21A2C4",
            "color": "#B1F1FF",
            "weight": 2.5,
            "fillOpacity": 0.8
        }
    ).add_to(m)

    add_click_markers(m, geojson, "region")

    bounds = []

    for feature in geojson.get("features", []):
        center = coordinates_average(feature.get("geometry"))

        if center:
            bounds.append(center)

    if bounds:
        m.fit_bounds(bounds)

    map_style(m)

    return m


def render_trend(trend, number=None):
    title = trend["topic"]
    traffic = trend.get("traffic", "")
    countries = trend.get("countries", set())

    st.markdown(
        f"""
        <div class="story-card">
            <div class="story-title">
                {f"{number}. " if number else ""}{title}
            </div>
            <div class="story-meta">
                {traffic if traffic else "Traffic unavailable"}
                {(" · " + str(len(countries)) + " countries") if countries else ""}
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )


if "country" not in st.session_state:
    st.session_state.country = None

if "region" not in st.session_state:
    st.session_state.region = None

if "show_ten" not in st.session_state:
    st.session_state.show_ten = False

if "map_level" not in st.session_state:
    st.session_state.map_level = "world"


st.markdown("## LOCATIONAL SEARCH")
st.caption("LIVE SEARCH TRENDS BY LOCATION")

left, center, right = st.columns(
    [1.05, 2.7, 1.15],
    gap="small"
)


with left:
    st.markdown("### GLOBAL TRENDING SEARCHES")

    st.caption("Combined trends from selected countries")

    search_filter = st.text_input(
        "Filter topics",
        placeholder="Find a trend..."
    )

    try:
        with st.spinner("Collecting global trends..."):
            global_trends = get_global_trends()

        if search_filter:
            global_trends = [
                t for t in global_trends
                if search_filter.casefold() in t["topic"].casefold()
            ]

        for index, trend in enumerate(global_trends[:10], 1):
            render_trend(trend, index)

        if not global_trends:
            st.info("No trends are available right now.")

    except Exception as exc:
        st.error(f"Global trends unavailable: {exc}")


with center:
    if st.session_state.country:
        country = st.session_state.country

        map_title, map_button = st.columns([3, 1])

        with map_title:
            st.markdown(f"### {country['name']}")

        with map_button:
            if st.button("World map"):
                st.session_state.country = None
                st.session_state.region = None
                st.session_state.map_level = "world"
                st.rerun()

        try:
            with st.spinner("Loading regional boundaries..."):
                metadata, geojson = get_regions(country["alpha3"])

            m = region_map(geojson)

            map_result = st_folium(
                m,
                height=690,
                width=None,
                key=f"regions_{country['alpha3']}",
                returned_objects=["last_object_clicked_tooltip"]
            )

            clicked = map_result.get("last_object_clicked_tooltip")

            if clicked and "|" in clicked:
                region_name, region_code = clicked.split("|", 1)

                if region_name != st.session_state.region:
                    st.session_state.region = {
                        "name": region_name,
                        "code": region_code.strip()
                    }
                    st.rerun()

        except Exception as exc:
            st.error(f"Regional map unavailable: {exc}")

    else:
        st.markdown("### WORLD MAP")

        try:
            world = get_world()
            m = world_map(world)

            map_result = st_folium(
                m,
                height=690,
                width=None,
                key="world_map",
                returned_objects=["last_object_clicked_tooltip"]
            )

            clicked = map_result.get("last_object_clicked_tooltip")

            if clicked and "|" in clicked:
                name, alpha2, alpha3 = clicked.split("|", 2)

                if alpha2 and alpha3:
                    st.session_state.country = {
                        "name": name,
                        "alpha2": alpha2,
                        "alpha3": alpha3
                    }
                    st.session_state.region = None
                    st.session_state.map_level = "country"
                    st.rerun()

        except Exception as exc:
            st.error(f"World map unavailable: {exc}")


with right:
    if not st.session_state.country:
        st.markdown("### LOCATION")
        st.divider()
        st.write("Click a country on the map to explore its searches.")

    else:
        country = st.session_state.country

        if st.session_state.region:
            location_name = st.session_state.region["name"]
            region_code = st.session_state.region["code"]

            st.markdown("### SELECTED STATE / REGION")
            st.markdown(f"## {location_name}")
            st.caption(country["name"])

            if st.button("Back to country trends"):
                st.session_state.region = None
                st.rerun()

            if region_code and "-" in region_code:
                trend_geo = region_code
            else:
                trend_geo = None

        else:
            location_name = country["name"]
            trend_geo = country["alpha2"]

            st.markdown("### COUNTRY")
            st.markdown(f"## {location_name}")
            st.caption(country["alpha2"])

        st.divider()

        if trend_geo:
            try:
                with st.spinner("Loading location trends..."):
                    local_trends = get_trends(trend_geo)

                if not local_trends:
                    st.info(
                        "Google Trends has no separate trending feed "
                        "for this location."
                    )

                else:
                    visible = (
                        local_trends[:10]
                        if st.session_state.show_ten
                        else local_trends[:5]
                    )

                    st.markdown("### TRENDING SEARCHES")

                    for index, trend in enumerate(visible, 1):
                        render_trend(trend, index)

                    if len(local_trends) > 5:
                        if st.button(
                            "Show top 10" if not st.session_state.show_ten
                            else "Show top 5"
                        ):
                            st.session_state.show_ten = (
                                not st.session_state.show_ten
                            )
                            st.rerun()

                    st.divider()

                    selected_topic = st.selectbox(
                        "Choose a trending topic",
                        [t["topic"] for t in local_trends]
                    )

                    selected = next(
                        t for t in local_trends
                        if t["topic"] == selected_topic
                    )

                    st.markdown("#### RELATED NEWS")

                    if selected["news"]:
                        for item in selected["news"][:3]:
                            if item["url"]:
                                st.markdown(
                                    f"[{item['title']}]({item['url']})"
                                )
                            else:
                                st.write(item["title"])
                    else:
                        st.caption("No related news supplied.")

            except Exception as exc:
                st.warning(
                    "Separate search trends are unavailable "
                    f"for this location: {exc}"
                )
        else:
            st.info(
                "This region does not have a separate Google Trends "
                "location code. Country-level trends are not being "
                "presented as regional results."
            )


st.divider()

st.caption(
    "Search trend source: Google Trends RSS. "
    "Geographic boundaries: geoBoundaries. "
    "Traffic figures are approximate, not exact search counts."
)
