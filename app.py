
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

import folium
import pycountry
import requests
import streamlit as st
from streamlit_folium import st_folium


# --------------------------------------------------
# PAGE SETTINGS
# --------------------------------------------------

st.set_page_config(
    page_title="Locational Search",
    page_icon="🌍",
    layout="wide",
    initial_sidebar_state="collapsed"
)

RSS_URL = "https://trends.google.com/trending/rss"
WORLD_URL = (
    "https://raw.githubusercontent.com/"
    "datasets/geo-countries/master/data/countries.geojson"
)
BOUNDARY_URL = "https://www.geoboundaries.org/api/current/gbOpen"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 Chrome/130.0 Safari/537.36"
}

GLOBAL_COUNTRIES = [
    "US", "GB", "DE", "IN", "JP",
    "BR", "CA", "AU", "FR", "NL"
]

COUNTRY_NAMES = {
    country.alpha_2: country.name
    for country in pycountry.countries
}


# --------------------------------------------------
# DESIGN
# --------------------------------------------------

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

[data-testid="stMetric"] {
    background: #111C2B;
    border: 1px solid #26384B;
    border-radius: 8px;
    padding: 12px;
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
    line-height: 1.5;
}

.story-meta {
    color: #91A4B8;
    font-size: 11px;
    margin-top: 6px;
}

.section-label {
    color: #73CBE8;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
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

.leaflet-container {
    background: #0B1420 !important;
    border: 1px solid #26384B;
    border-radius: 8px;
}

footer {
    visibility: hidden;
}
</style>
""", unsafe_allow_html=True)


# --------------------------------------------------
# HTTP REQUESTS
# --------------------------------------------------

def fetch(url, params=None, timeout=45):
    response = requests.get(
        url,
        params=params,
        headers=HEADERS,
        timeout=timeout
    )

    response.raise_for_status()
    return response


# --------------------------------------------------
# GOOGLE TRENDS RSS
# --------------------------------------------------

@st.cache_data(ttl=1800, show_spinner=False)
def get_trends(geo):
    """
    Get the current Google Trending Now RSS feed
    for a country or supported regional location.
    """

    response = fetch(
        RSS_URL,
        params={"geo": geo}
    )

    root = ET.fromstring(response.content)

    namespace = {
        "ht": "https://trends.google.com/trending/rss"
    }

    results = []

    for item in root.findall(".//item"):
        title = item.findtext("title", default="").strip()

        if not title:
            continue

        traffic = item.findtext(
            "ht:approx_traffic",
            default="",
            namespaces=namespace
        )

        news = []

        for news_item in item.findall("ht:news_item", namespace):
            news_title = news_item.findtext(
                "ht:news_item_title",
                default="",
                namespaces=namespace
            )

            news_url = news_item.findtext(
                "ht:news_item_url",
                default="",
                namespaces=namespace
            )

            news_source = news_item.findtext(
                "ht:news_item_source",
                default="",
                namespaces=namespace
            )

            if news_title:
                news.append({
                    "title": news_title,
                    "url": news_url,
                    "source": news_source
                })

        results.append({
            "topic": title,
            "traffic": traffic,
            "news": news,
            "published": item.findtext("pubDate", default="")
        })

    return results


def traffic_value(value):
    """Convert approximate traffic labels into sortable numbers."""

    if not value:
        return 0

    value = value.upper().replace(",", "").replace("+", "")

    match = re.search(r"([\d.]+)\s*([KMB]?)", value)

    if not match:
        return 0

    number = float(match.group(1))

    multiplier = {
        "": 1,
        "K": 1000,
        "M": 1000000,
        "B": 1000000000
    }

    return int(number * multiplier.get(match.group(2), 1))


# --------------------------------------------------
# GLOBAL TOP 10
# --------------------------------------------------

@st.cache_data(ttl=1800, show_spinner=False)
def get_global_trends():
    """
    Collect trends from multiple countries.

    Topics appearing in more countries rank higher.
    Approximate traffic is used as a secondary ranking.
    """

    collected = []

    with ThreadPoolExecutor(max_workers=3) as executor:

        futures = {
            executor.submit(get_trends, code): code
            for code in GLOBAL_COUNTRIES
        }

        for future in as_completed(futures):
            code = futures[future]

            try:
                trends = future.result()

                for trend in trends:
                    trend["country_code"] = code
                    trend["country_name"] = COUNTRY_NAMES.get(
                        code, code
                    )

                    collected.append(trend)

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
                "traffic_number": traffic_value(
                    trend["traffic"]
                ),
                "news": trend["news"]
            }

        grouped[key]["countries"].add(
            trend["country_code"]
        )

        current_traffic = traffic_value(
            trend["traffic"]
        )

        if current_traffic > grouped[key]["traffic_number"]:
            grouped[key]["traffic"] = trend["traffic"]
            grouped[key]["traffic_number"] = current_traffic

        if not grouped[key]["news"] and trend["news"]:
            grouped[key]["news"] = trend["news"]

    results = list(grouped.values())

    results.sort(
        key=lambda item: (
            len(item["countries"]),
            item["traffic_number"]
        ),
        reverse=True
    )

    return results[:10]


# --------------------------------------------------
# WORLD MAP DATA
# --------------------------------------------------

@st.cache_data(ttl=86400, show_spinner=False)
def get_world():
    response = fetch(WORLD_URL, timeout=60)
    return response.json()


# --------------------------------------------------
# COUNTRY REGIONAL BOUNDARIES
# --------------------------------------------------

@st.cache_data(ttl=86400, show_spinner=False)
def get_regions(country_code):
    """
    Download first-level administrative boundaries
    for the selected country.
    """

    metadata_response = fetch(
        f"{BOUNDARY_URL}/{country_code}/ADM1/"
    )

    metadata = metadata_response.json()

    geometry_url = (
        metadata.get("simplifiedGeometryGeoJSON")
        or metadata.get("gjDownloadURL")
    )

    if not geometry_url:
        raise RuntimeError(
            "Regional boundaries are not available."
        )

    geometry_response = fetch(
        geometry_url,
        timeout=60
    )

    return geometry_response.json()


# --------------------------------------------------
# COUNTRY IDENTIFICATION
# --------------------------------------------------

def identify_country(properties):
    """
    Read ISO country codes from the world map.
    """

    code_fields = [
        "ISO_A3",
        "ADM0_A3",
        "ISO_A3_EH",
        "SOV_A3"
    ]

    for field in code_fields:
        code = properties.get(field)

        if code and code != "-99":
            country = pycountry.countries.get(
                alpha_3=code
            )

            if country:
                return {
                    "name": country.name,
                    "alpha2": country.alpha_2,
                    "alpha3": country.alpha_3
                }

    name_fields = [
        "ADMIN",
        "name",
        "NAME",
        "SOVEREIGNT"
    ]

    for field in name_fields:
        name = properties.get(field)

        if name:
            try:
                country = pycountry.countries.lookup(name)

                return {
                    "name": country.name,
                    "alpha2": country.alpha_2,
                    "alpha3": country.alpha_3
                }

            except LookupError:
                continue

    return None


# --------------------------------------------------
# REGION IDENTIFICATION
# --------------------------------------------------

def region_name(properties):
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


def region_google_code(properties, country_code):
    """
    Find the ISO 3166-2 regional code where available.
    Example: US-CA for California.
    """

    possible_fields = [
        "shapeISO",
        "shapeISO2",
        "ISO_3166_2",
        "iso_3166_2"
    ]

    for field in possible_fields:
        value = properties.get(field)

        if value and "-" in str(value):
            value = str(value).upper()
            prefix, suffix = value.split("-", 1)

            country = pycountry.countries.get(
                alpha_3=country_code
            )

            if country and prefix == country.alpha_3:
                value = f"{country.alpha_2}-{suffix}"

            return value

    return None


# --------------------------------------------------
# MAP HELPERS
# --------------------------------------------------

def average_coordinates(geometry):
    """
    Calculate a simple average coordinate for a feature.
    Used to calculate the map bounds.
    """

    points = []

    def collect(value):
        if (
            isinstance(value, list)
            and len(value) >= 2
            and isinstance(value[0], (int, float))
            and isinstance(value[1], (int, float))
        ):
            points.append((value[1], value[0]))

        elif isinstance(value, list):
            for child in value:
                collect(child)

    if geometry:
        collect(geometry.get("coordinates", []))

    if not points:
        return None

    return (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points)
    )


def prepare_country_features(geojson):
    """
    Add a readable hover label to each country polygon.
    The polygon itself is the click target.
    """

    for feature in geojson.get("features", []):
        country = identify_country(
            feature.get("properties", {})
        )

        if country:
            feature.setdefault("properties", {})[
                "_map_label"
            ] = country["name"]


def prepare_region_features(geojson):
    """
    Add a readable hover label to each state/province polygon.
    The polygon itself is the click target.
    """

    for feature in geojson.get("features", []):
        name = region_name(
            feature.get("properties", {})
        )

        if name:
            feature.setdefault("properties", {})[
                "_map_label"
            ] = name


# --------------------------------------------------
# MAP CREATION
# --------------------------------------------------

def create_world_map(world):
    """
    Draw the world without external map tiles.
    This removes Carto API-key watermarks.
    """

    m = folium.Map(
        location=[20, 0],
        zoom_start=2,
        tiles=None,
        control_scale=True,
        prefer_canvas=True
    )

    m.get_root().html.add_child(
        folium.Element("""
        <style>
        .leaflet-container {
            background: #0B1420 !important;
        }
        </style>
        """)
    )

    prepare_country_features(world)

    folium.GeoJson(
        world,
        name="Countries",
        tooltip=folium.GeoJsonTooltip(
            fields=["_map_label"],
            aliases=["Country:"],
            sticky=True,
            style="background-color: #111C2B; color: #E5EDF5; "
                  "border: 1px solid #4E9DB9; border-radius: 4px;"
        ),
        style_function=lambda feature: {
            "fillColor": "#173B52",
            "color": "#4E9DB9",
            "weight": 0.65,
            "fillOpacity": 0.7
        },
        highlight_function=lambda feature: {
            "fillColor": "#1B789B",
            "color": "#A8EDFF",
            "weight": 2,
            "fillOpacity": 0.9
        }
    ).add_to(m)

    return m


def create_region_map(geojson):
    """
    Draw the selected country's administrative divisions.
    """

    m = folium.Map(
        location=[20, 0],
        zoom_start=4,
        tiles=None,
        control_scale=True,
        prefer_canvas=True
    )

    m.get_root().html.add_child(
        folium.Element("""
        <style>
        .leaflet-container {
            background: #0B1420 !important;
        }
        </style>
        """)
    )

    prepare_region_features(geojson)

    folium.GeoJson(
        geojson,
        name="States and regions",
        tooltip=folium.GeoJsonTooltip(
            fields=["_map_label"],
            aliases=["State / region:"],
            sticky=True,
            style="background-color: #111C2B; color: #E5EDF5; "
                  "border: 1px solid #65C7E7; border-radius: 4px;"
        ),
        style_function=lambda feature: {
            "fillColor": "#15516E",
            "color": "#65C7E7",
            "weight": 1.2,
            "fillOpacity": 0.75
        },
        highlight_function=lambda feature: {
            "fillColor": "#218FB5",
            "color": "#C1F2FF",
            "weight": 2.5,
            "fillOpacity": 0.95
        }
    ).add_to(m)

    bounds = []

    for feature in geojson.get("features", []):
        center = average_coordinates(
            feature.get("geometry")
        )

        if center:
            bounds.append(center)

    if bounds:
        m.fit_bounds(bounds)

    return m


# --------------------------------------------------
# DISPLAY SEARCH RESULTS
# --------------------------------------------------

def display_trend(trend, number=None):
    topic = str(trend.get("topic", ""))
    traffic = str(trend.get("traffic") or "Not available")
    countries = trend.get("countries", set())
    country_count = len(countries)

    number_text = f"{number}. " if number is not None else ""

    with st.container(border=True):
        st.markdown(f"**{number_text}{topic}**")

        meta = f"Approx. traffic: {traffic}"

        if countries:
            meta += f" · Appears in {country_count} countries"

        st.caption(meta)


# --------------------------------------------------
# SESSION STATE
# --------------------------------------------------

if "selected_country" not in st.session_state:
    st.session_state.selected_country = None

if "selected_region" not in st.session_state:
    st.session_state.selected_region = None

if "show_ten" not in st.session_state:
    st.session_state.show_ten = False


# --------------------------------------------------
# HEADER
# --------------------------------------------------

st.markdown("# LOCATIONAL SEARCH")

st.caption(
    "EXPLORE WHAT PEOPLE ARE SEARCHING FOR AROUND THE WORLD"
)

st.divider()


# --------------------------------------------------
# THREE-PANEL LAYOUT
# --------------------------------------------------

left, center, right = st.columns(
    [1.05, 2.7, 1.15],
    gap="small"
)


# --------------------------------------------------
# LEFT: GLOBAL TOP 10
# --------------------------------------------------

with left:

    st.markdown("### GLOBAL TOP 10 SEARCHES")

    st.caption(
        "Combined live trends from multiple countries"
    )

    try:
        with st.spinner("Collecting global searches..."):
            global_trends = get_global_trends()

        if not global_trends:
            st.warning(
                "Global search trends are temporarily unavailable."
            )

        else:
            for index, trend in enumerate(
                global_trends[:10],
                start=1
            ):
                display_trend(trend, index)

    except Exception:
        st.warning(
            "Could not load global searches. "
            "Please try again later."
        )


# --------------------------------------------------
# CENTER: INTERACTIVE MAP
# --------------------------------------------------

with center:

    selected_country = st.session_state.selected_country

    if selected_country:

        title_col, button_col = st.columns([3, 1])

        with title_col:
            st.markdown(
                f"### {selected_country['name']}"
            )

        with button_col:
            if st.button(
                "World map",
                use_container_width=True
            ):
                st.session_state.selected_country = None
                st.session_state.selected_region = None
                st.session_state.show_ten = False
                st.rerun()

        try:
            with st.spinner(
                "Loading state and province boundaries..."
            ):

                regional_data = get_regions(
                    selected_country["alpha3"]
                )

            regional_map = create_region_map(
                regional_data
            )

            map_result = st_folium(
                regional_map,
                height=690,
                width=None,
                key=f"regional_map_{selected_country['alpha3']}",
                returned_objects=["last_active_drawing"]
            )

            active_feature = (
                map_result.get("last_active_drawing") or {}
            )

            properties = (
                active_feature.get("properties", {})
                if isinstance(active_feature, dict)
                else {}
            )

            name = (
                region_name(properties)
                if properties
                else None
            )

            if name:
                google_code = region_google_code(
                    properties,
                    selected_country["alpha3"]
                )

                new_region = {
                    "name": name,
                    "google_code": google_code
                }

                if (
                    new_region
                    != st.session_state.selected_region
                ):
                    st.session_state.selected_region = new_region
                    st.session_state.show_ten = False
                    st.rerun()

        except Exception as exc:

            st.error(
                "Could not load this country's regional map."
            )

            st.caption(str(exc))

    else:

        st.markdown("### WORLD MAP")

        try:
            world = get_world()

            world_map = create_world_map(world)

            map_result = st_folium(
                world_map,
                height=690,
                width=None,
                key="main_world_map",
                returned_objects=["last_active_drawing"]
            )

            active_feature = (
                map_result.get("last_active_drawing") or {}
            )

            properties = (
                active_feature.get("properties", {})
                if isinstance(active_feature, dict)
                else {}
            )

            country = (
                identify_country(properties)
                if properties
                else None
            )

            if country:
                new_country = country

                if (
                    new_country
                    != st.session_state.selected_country
                ):
                    st.session_state.selected_country = new_country
                    st.session_state.selected_region = None
                    st.session_state.show_ten = False
                    st.rerun()

        except Exception as exc:

            st.error("Could not load the world map.")

            st.caption(str(exc))


# --------------------------------------------------
# RIGHT: SELECTED LOCATION SEARCHES
# --------------------------------------------------

with right:

    selected_country = st.session_state.selected_country
    selected_region = st.session_state.selected_region

    if not selected_country:

        st.markdown("### LOCATION")

        st.divider()

        st.write(
            "Select a country on the map to explore "
            "its trending searches."
        )

    else:

        if selected_region:

            location_name = selected_region["name"]

            google_geo = selected_region["google_code"]

            st.markdown("### SELECTED REGION")

            st.markdown(f"## {location_name}")

            st.caption(selected_country["name"])

            if st.button(
                "Back to country",
                use_container_width=True
            ):
                st.session_state.selected_region = None
                st.session_state.show_ten = False
                st.rerun()

        else:

            location_name = selected_country["name"]

            google_geo = selected_country["alpha2"]

            st.markdown("### COUNTRY")

            st.markdown(f"## {location_name}")

            st.caption(selected_country["alpha2"])

        st.divider()

        if not google_geo:

            st.info(
                "A separate Google Trends feed is not available "
                "for this region."
            )

        else:

            try:

                with st.spinner(
                    f"Loading searches for {location_name}..."
                ):

                    local_trends = get_trends(google_geo)

                if not local_trends:

                    st.info(
                        "No trending searches were returned "
                        "for this location."
                    )

                else:

                    st.markdown("### TOP SEARCHES")

                    visible_count = (
                        10
                        if st.session_state.show_ten
                        else 5
                    )

                    for index, trend in enumerate(
                        local_trends[:visible_count],
                        start=1
                    ):
                        display_trend(trend, index)

                    if len(local_trends) > 5:

                        if st.button(
                            "Show top 5"
                            if st.session_state.show_ten
                            else "Show top 10",
                            use_container_width=True
                        ):

                            st.session_state.show_ten = (
                                not st.session_state.show_ten
                            )

                            st.rerun()

                    st.divider()

                    st.markdown("### RELATED NEWS")

                    topics = [
                        trend["topic"]
                        for trend in local_trends
                    ]

                    selected_topic = st.selectbox(
                        "Select a trending search",
                        topics,
                        label_visibility="collapsed"
                    )

                    selected_trend = next(
                        trend
                        for trend in local_trends
                        if trend["topic"] == selected_topic
                    )

                    if selected_trend["news"]:

                        for article in selected_trend["news"][:3]:

                            article_title = article["title"]
                            article_url = article["url"]
                            article_source = article["source"]

                            if article_url:
                                st.markdown(
                                    f"[{article_title}]({article_url})"
                                )
                            else:
                                st.write(article_title)

                            if article_source:
                                st.caption(article_source)

                    else:

                        st.caption(
                            "No related news articles were provided."
                        )

            except requests.HTTPError as exc:

                status = (
                    exc.response.status_code
                    if exc.response is not None
                    else "unknown"
                )

                if status == 400:

                    st.info(
                        "Google Trends does not provide a trending "
                        "search feed for this location."
                    )

                elif status == 429:

                    st.warning(
                        "Google Trends is temporarily limiting requests. "
                        "Please wait and try again."
                    )

                else:

                    st.warning(
                        "Search data is temporarily unavailable."
                    )

            except requests.RequestException:

                st.warning(
                    "Could not connect to Google Trends. "
                    "Please try again later."
                )

            except Exception:

                st.warning(
                    "Search data is temporarily unavailable."
                )


# --------------------------------------------------
# FOOTER
# --------------------------------------------------

st.divider()

st.caption(
    "Search data: Google Trends Trending Now RSS. "
    "Map boundaries: geoBoundaries. "
    "Global ranking is an aggregation of selected country feeds. "
    "Traffic figures are approximate, not exact search counts."
)
