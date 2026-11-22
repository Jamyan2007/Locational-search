
import random
import threading
import time

import folium
import pandas as pd
import pycountry
import requests
import streamlit as st
from streamlit_folium import st_folium

st.set_page_config(
    page_title="Locational Search",
    layout="wide",
    initial_sidebar_state="collapsed"
)

WORLD_URL = (
    "https://raw.githubusercontent.com/"
    "datasets/geo-countries/master/data/countries.geojson"
)

GEO_URL = "https://www.geoboundaries.org/api/current/gbOpen"
GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

HEADERS = {"User-Agent": "LocationalSearch/1.0"}

st.markdown("""
<style>
.stApp {
    background: #080E18;
    color: #E5EDF5;
}
.block-container {
    padding: 1rem 1.2rem;
    max-width: 1800px;
}
h1, h2, h3 {
    color: #E5EDF5;
}
[data-testid="stMetric"] {
    background: #111C2B;
    border: 1px solid #26384B;
    padding: 12px;
    border-radius: 8px;
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
[data-testid="stTextInput"] input {
    background: #111C2B;
    color: white;
    border: 1px solid #30465D;
}
hr {
    border-color: #26384B;
}
.story {
    background: #111C2B;
    border: 1px solid #26384B;
    border-radius: 7px;
    padding: 10px;
    margin-bottom: 8px;
}
.muted {
    color: #91A4B8;
    font-size: 12px;
}
</style>
""", unsafe_allow_html=True)


@st.cache_resource
def rate_limiter():
    return {
        "lock": threading.Lock(),
        "last": 0.0
    }


def gdelt_request(params):
    limiter = rate_limiter()

    with limiter["lock"]:
        delay = 5 - (time.monotonic() - limiter["last"])

        if delay > 0:
            time.sleep(delay)

        response = requests.get(
            GDELT_URL,
            params=params,
            headers=HEADERS,
            timeout=45
        )

        limiter["last"] = time.monotonic()

    if response.status_code == 429:
        raise RuntimeError("GDELT rate limit reached. Try again shortly.")

    response.raise_for_status()

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError("GDELT returned an invalid response.") from exc


@st.cache_data(ttl=3600, show_spinner=False)
def get_world():
    response = requests.get(WORLD_URL, headers=HEADERS, timeout=60)
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=86400, show_spinner=False)
def get_country_regions(code):
    url = f"{GEO_URL}/{code}/ADM1/"
    response = requests.get(url, headers=HEADERS, timeout=45)
    response.raise_for_status()
    metadata = response.json()

    geometry_url = (
        metadata.get("simplifiedGeometryGeoJSON")
        or metadata.get("gjDownloadURL")
    )

    if not geometry_url:
        raise RuntimeError("No regional map is available for this country.")

    geometry_response = requests.get(
        geometry_url,
        headers=HEADERS,
        timeout=60
    )
    geometry_response.raise_for_status()

    return metadata, geometry_response.json()


@st.cache_data(ttl=600, show_spinner=False)
def get_global_stories():
    params = {
        "query": (
            "(election OR government OR conflict OR economy "
            "OR climate OR technology OR health OR diplomacy "
            "OR energy OR security)"
        ),
        "mode": "ArtList",
        "format": "json",
        "maxrecords": 10,
        "timespan": "24h",
        "sort": "DateDesc"
    }

    data = gdelt_request(params)
    return data.get("articles", [])


@st.cache_data(ttl=600, show_spinner=False)
def get_article_volume(query, period):
    params = {
        "query": query,
        "mode": "TimelineVolRaw",
        "format": "json",
        "timespan": period
    }

    data = gdelt_request(params)

    total = 0
    found = False

    for series in data.get("timeline", []):
        for point in series.get("data", []):
            try:
                total += int(float(point["value"]))
                found = True
            except (KeyError, TypeError, ValueError):
                pass

    return total if found else None


@st.cache_data(ttl=600, show_spinner=False)
def get_headlines(query, period):
    params = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": 10,
        "timespan": period,
        "sort": "DateDesc"
    }

    data = gdelt_request(params)
    return data.get("articles", [])


def country_from_feature(properties):
    code_fields = [
        "ISO3166-1-Alpha-3",
        "ISO_A3",
        "ADM0_A3",
        "ISO_A3_EH"
    ]

    name_fields = [
        "ADMIN",
        "name",
        "NAME",
        "SOVEREIGNT",
        "NAME_EN"
    ]

    for field in code_fields:
        code = properties.get(field)

        if code and code != "-99":
            country = pycountry.countries.get(alpha_3=code)

            if country:
                return country.name, country.alpha_3

    for field in name_fields:
        name = properties.get(field)

        if name:
            try:
                country = pycountry.countries.lookup(name)
                return country.name, country.alpha_3
            except LookupError:
                pass

    return None, None


def get_property_field(feature, candidates):
    properties = feature.get("properties", {})

    for field in candidates:
        if field in properties:
            return field

    return None


def geometry_bounds(geojson):
    points = []

    def collect(item):
        if (
            isinstance(item, list)
            and len(item) >= 2
            and isinstance(item[0], (int, float))
            and isinstance(item[1], (int, float))
        ):
            points.append((item[1], item[0]))
        elif isinstance(item, list):
            for child in item:
                collect(child)

    for feature in geojson.get("features", []):
        geometry = feature.get("geometry") or {}
        collect(geometry.get("coordinates", []))

    if not points:
        return None

    latitudes = [point[0] for point in points]
    longitudes = [point[1] for point in points]

    return [
        [min(latitudes), min(longitudes)],
        [max(latitudes), max(longitudes)]
    ]


def make_world_map(world_data):
    map_object = folium.Map(
        location=[20, 0],
        zoom_start=2,
        tiles="CartoDB dark_matter",
        control_scale=True,
        prefer_canvas=True
    )

    first_feature = world_data["features"][0]

    name_field = get_property_field(
        first_feature,
        ["ADMIN", "name", "NAME", "SOVEREIGNT"]
    )

    folium.GeoJson(
        world_data,
        name="Countries",
        style_function=lambda feature: {
            "fillColor": "#152B40",
            "color": "#50657B",
            "weight": 0.65,
            "fillOpacity": 0.55
        },
        highlight_function=lambda feature: {
            "fillColor": "#167E9D",
            "color": "#66D9F0",
            "weight": 2,
            "fillOpacity": 0.8
        },
        tooltip=(
            folium.GeoJsonTooltip(
                fields=[name_field],
                aliases=["Country:"],
                sticky=True
            )
            if name_field else None
        )
    ).add_to(map_object)

    return map_object


def make_region_map(geojson, country_name):
    map_object = folium.Map(
        location=[20, 0],
        zoom_start=4,
        tiles="CartoDB dark_matter",
        control_scale=True,
        prefer_canvas=True
    )

    if not geojson.get("features"):
        return map_object

    name_field = get_property_field(
        geojson["features"][0],
        ["shapeName", "boundaryName", "name", "NAME_1"]
    )

    folium.GeoJson(
        geojson,
        name="Administrative regions",
        style_function=lambda feature: {
            "fillColor": "#155D83",
            "color": "#76C9E5",
            "weight": 1.2,
            "fillOpacity": 0.55
        },
        highlight_function=lambda feature: {
            "fillColor": "#21A2C4",
            "color": "#B1F1FF",
            "weight": 2.5,
            "fillOpacity": 0.85
        },
        tooltip=(
            folium.GeoJsonTooltip(
                fields=[name_field],
                aliases=["Region:"],
                sticky=True
            )
            if name_field else None
        )
    ).add_to(map_object)

    bounds = geometry_bounds(geojson)

    if bounds:
        map_object.fit_bounds(bounds)

    return map_object


def get_region_names(geojson):
    names = []

    for feature in geojson.get("features", []):
        properties = feature.get("properties", {})

        name = (
            properties.get("shapeName")
            or properties.get("boundaryName")
            or properties.get("name")
            or properties.get("NAME_1")
        )

        if name:
            names.append(str(name))

    return sorted(set(names), key=str.casefold)


if "selected_country" not in st.session_state:
    st.session_state.selected_country = None

if "selected_region" not in st.session_state:
    st.session_state.selected_region = None

if "show_all_regions" not in st.session_state:
    st.session_state.show_all_regions = False

if "period" not in st.session_state:
    st.session_state.period = "7d"


header_left, header_right = st.columns([3, 1])

with header_left:
    st.markdown("## LOCATIONAL SEARCH")
    st.caption("GLOBAL NEWS AND LOCATION EXPLORER")

with header_right:
    st.session_state.period = st.selectbox(
        "News period",
        ["24h", "3d", "7d"],
        index=["24h", "3d", "7d"].index(
            st.session_state.period
        ),
        format_func=lambda value: {
            "24h": "Last 24 hours",
            "3d": "Last 3 days",
            "7d": "Last 7 days"
        }[value]
    )

period = st.session_state.period

try:
    world_data = get_world()
except requests.RequestException as exc:
    st.error(f"Could not load the world map: {exc}")
    st.stop()


left, center, right = st.columns(
    [1.05, 2.7, 1.15],
    gap="small"
)


with left:
    st.markdown("### GLOBAL TOP 10")

    story_search = st.text_input(
        "Search stories",
        placeholder="Search headlines..."
    )

    try:
        with st.spinner("Loading global stories..."):
            stories = get_global_stories()

        if story_search:
            stories = [
                story for story in stories
                if story_search.lower() in
                story.get("title", "").lower()
            ]

        for index, story in enumerate(stories[:10], start=1):
            title = story.get("title", "Untitled story")
            url = story.get("url", "")
            source = story.get("domain", "")

            st.markdown(
                f"""
                <div class="story">
                    <div class="muted">STORY {index:02d}</div>
                    <div style="font-size:13px;font-weight:600;">
                        <a href="{url}" target="_blank"
                        style="color:#DCEAF5;text-decoration:none;">
                        {title}
                        </a>
                    </div>
                    <div class="muted">{source}</div>
                </div>
                """,
                unsafe_allow_html=True
            )

        if not stories:
            st.info("No matching stories.")

    except (requests.RequestException, RuntimeError) as exc:
        st.error(f"Global stories unavailable: {exc}")


with center:
    if st.session_state.selected_country:
        selected_name = st.session_state.selected_country["name"]

        map_title, map_action = st.columns([3, 1])

        with map_title:
            st.markdown(f"### {selected_name}")

        with map_action:
            if st.button("World map"):
                st.session_state.selected_country = None
                st.session_state.selected_region = None
                st.session_state.show_all_regions = False
                st.rerun()

        try:
            with st.spinner("Loading regional map..."):
                metadata, regional_geojson = get_country_regions(
                    st.session_state.selected_country["code"]
                )

            region_map = make_region_map(
                regional_geojson,
                selected_name
            )

            map_result = st_folium(
                region_map,
                height=690,
                width=None,
                key=f"region_map_{st.session_state.selected_country['code']}",
                returned_objects=["last_active_drawing"]
            )

            clicked = map_result.get("last_active_drawing")

            if clicked:
                properties = clicked.get("properties", {})

                region_name = (
                    properties.get("shapeName")
                    or properties.get("boundaryName")
                    or properties.get("name")
                    or properties.get("NAME_1")
                )

                if region_name:
                    if region_name != st.session_state.selected_region:
                        st.session_state.selected_region = region_name
                        st.rerun()

        except (requests.RequestException, RuntimeError, KeyError) as exc:
            st.error(f"Could not load regional boundaries: {exc}")

    else:
        st.markdown("### WORLD MAP")

        world_map = make_world_map(world_data)

        map_result = st_folium(
            world_map,
            height=690,
            width=None,
            key="world_map",
            returned_objects=["last_active_drawing"]
        )

        clicked = map_result.get("last_active_drawing")

        if clicked:
            properties = clicked.get("properties", {})
            name, code = country_from_feature(properties)

            if name and code:
                st.session_state.selected_country = {
                    "name": name,
                    "code": code
                }
                st.session_state.selected_region = None
                st.session_state.show_all_regions = False
                st.rerun()


with right:
    if not st.session_state.selected_country:
        st.markdown("### LOCATION")
        st.markdown("---")
        st.markdown("Select a country on the map.")
        st.caption(
            "Its regions and news coverage will appear here."
        )

    else:
        country = st.session_state.selected_country
        country_name = country["name"]

        st.markdown("### COUNTRY")
        st.markdown(f"## {country_name}")
        st.caption(country["code"])

        try:
            country_volume = get_article_volume(
                f'"{country_name}"',
                period
            )

            st.metric(
                "News article matches",
                (
                    f"{country_volume:,}"
                    if country_volume is not None
                    else "Unavailable"
                )
            )

        except (requests.RequestException, RuntimeError):
            st.metric("News article matches", "Unavailable")

        st.divider()

        try:
            metadata, regional_geojson = get_country_regions(
                country["code"]
            )

            regions = get_region_names(regional_geojson)

            st.markdown("### REGIONS")

            st.caption(f"{len(regions)} regions available")

            visible_regions = (
                regions
                if st.session_state.show_all_regions
                else regions[:5]
            )

            for region in visible_regions:
                if st.button(
                    region,
                    key=f"region_{country['code']}_{region}",
                    use_container_width=True
                ):
                    st.session_state.selected_region = region
                    st.rerun()

            if len(regions) > 5:
                if st.button(
                    "Show all regions"
                    if not st.session_state.show_all_regions
                    else "Show fewer regions",
                    use_container_width=True
                ):
                    st.session_state.show_all_regions = (
                        not st.session_state.show_all_regions
                    )
                    st.rerun()

            st.divider()

            if st.session_state.selected_region:
                region = st.session_state.selected_region

                st.markdown("### SELECTED REGION")
                st.markdown(f"**{region}**")

                region_query = (
                    f'"{region}" "{country_name}"'
                )

                try:
                    region_volume = get_article_volume(
                        region_query,
                        period
                    )

                    st.metric(
                        "News article matches",
                        (
                            f"{region_volume:,}"
                            if region_volume is not None
                            else "Unavailable"
                        )
                    )

                except (requests.RequestException, RuntimeError):
                    st.metric("News article matches", "Unavailable")

                try:
                    articles = get_headlines(
                        region_query,
                        period
                    )

                    st.markdown("#### HEADLINES")

                    if not articles:
                        st.caption("No matching headlines found.")

                    for article in articles[:5]:
                        title = article.get("title", "Untitled")
                        url = article.get("url", "")
                        source = article.get("domain", "")

                        if url:
                            st.markdown(
                                f"[{title}]({url})"
                            )
                        else:
                            st.write(title)

                        st.caption(source)

                except (requests.RequestException, RuntimeError):
                    st.caption("Headlines unavailable.")

                if st.button(
                    "Clear selected region",
                    use_container_width=True
                ):
                    st.session_state.selected_region = None
                    st.rerun()

        except (requests.RequestException, RuntimeError, KeyError) as exc:
            st.error(f"Region information unavailable: {exc}")


st.divider()

st.caption(
    "News data: GDELT Project DOC 2.0. "
    "Boundary data: geoBoundaries. "
    "News article matches are not the number of people searching online."
)
