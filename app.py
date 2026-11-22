
import random
import re
import threading
import time

import pandas as pd
import pycountry
import requests
import streamlit as st

st.set_page_config(
    page_title="Locational Search",
    layout="wide"
)

GEOBOUNDARIES_URL = "https://www.geoboundaries.org/api/current/gbOpen"
GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

COUNTRIES = sorted(
    [
        {
            "name": country.name,
            "code": country.alpha_3
        }
        for country in pycountry.countries
    ],
    key=lambda x: x["name"]
)

HEADERS = {
    "User-Agent": "LocationalSearch/1.0"
}


@st.cache_resource
def get_rate_limiter():
    return {
        "lock": threading.Lock(),
        "last_request": 0.0
    }


def get_json(url, params=None):
    response = requests.get(
        url,
        params=params,
        headers=HEADERS,
        timeout=60
    )
    response.raise_for_status()

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(
            "The API returned an invalid response."
        ) from exc


def gdelt_request(params):
    limiter = get_rate_limiter()

    with limiter["lock"]:
        wait = 5 - (
            time.monotonic() - limiter["last_request"]
        )

        if wait > 0:
            time.sleep(wait)

        response = requests.get(
            GDELT_URL,
            params=params,
            headers=HEADERS,
            timeout=60
        )

        limiter["last_request"] = time.monotonic()

    if response.status_code == 429:
        raise RuntimeError(
            "GDELT is busy. Please wait and try again."
        )

    response.raise_for_status()

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(
            "GDELT returned an invalid response."
        ) from exc


@st.cache_data(ttl=86400, show_spinner=False)
def get_regions(country_code):
    metadata_url = (
        f"{GEOBOUNDARIES_URL}/{country_code}/ADM1/"
    )

    metadata = get_json(metadata_url)

    geojson_url = (
        metadata.get("simplifiedGeometryGeoJSON")
        or metadata.get("gjDownloadURL")
    )

    if not geojson_url:
        raise RuntimeError(
            "No regional map data is available for this country."
        )

    geojson = get_json(geojson_url)

    regions = []

    for feature in geojson.get("features", []):
        properties = feature.get("properties", {})

        name = (
            properties.get("shapeName")
            or properties.get("boundaryName")
            or properties.get("name")
            or properties.get("NAME_1")
            or properties.get("NAME")
        )

        if name:
            regions.append(str(name))

    regions = sorted(set(regions), key=str.casefold)

    return {
        "regions": regions,
        "year": metadata.get("boundaryYearRepresented", "Not specified"),
        "source": metadata.get("boundarySource", "geoBoundaries"),
        "count": metadata.get("admUnitCount", len(regions))
    }


@st.cache_data(ttl=900, show_spinner=False)
def get_article_volume(query, timespan):
    params = {
        "query": query,
        "mode": "TimelineVolRaw",
        "format": "json",
        "timespan": timespan
    }

    data = gdelt_request(params)

    total = 0
    found = False

    for series in data.get("timeline", []):
        for point in series.get("data", []):
            value = point.get("value")

            if value is not None:
                try:
                    total += int(float(value))
                    found = True
                except (ValueError, TypeError):
                    pass

    return total if found else None


@st.cache_data(ttl=900, show_spinner=False)
def get_articles(query, timespan):
    params = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": 50,
        "timespan": timespan,
        "sort": "DateDesc"
    }

    data = gdelt_request(params)

    articles = data.get("articles", [])

    if not articles:
        return pd.DataFrame(
            columns=["Headline", "Publisher", "Date", "URL"]
        )

    df = pd.DataFrame(articles)

    for field in ["title", "domain", "seendate", "url"]:
        if field not in df.columns:
            df[field] = ""

    df = df.rename(columns={
        "title": "Headline",
        "domain": "Publisher",
        "seendate": "Date",
        "url": "URL"
    })

    df = df[
        ["Headline", "Publisher", "Date", "URL"]
    ].fillna("")

    return df.drop_duplicates(
        subset=["URL", "Headline"]
    )


st.title("Locational Search")

st.write(
    "Explore countries, their regions, and the news coverage "
    "connected to different locations around the world."
)

with st.sidebar:
    st.header("Search")

    if st.button("Random country", use_container_width=True):
        selected = random.choice(COUNTRIES)
        st.session_state["country_code"] = selected["code"]
        st.session_state.pop("selected_region", None)
        st.rerun()

    default_code = st.session_state.get(
        "country_code", "MNG"
    )

    country_codes = [c["code"] for c in COUNTRIES]

    if default_code not in country_codes:
        default_code = "MNG"

    country_index = country_codes.index(default_code)

    country = st.selectbox(
        "Choose a country",
        COUNTRIES,
        index=country_index,
        format_func=lambda c: c["name"],
        key="country_code"
    )

    period_label = st.selectbox(
        "News period",
        ["Last 24 hours", "Last 3 days", "Last 7 days"],
        index=2
    )

    period = {
        "Last 24 hours": "1d",
        "Last 3 days": "3d",
        "Last 7 days": "7d"
    }[period_label]


country_name = country["name"]
country_code = country["code"]

st.subheader(country_name)

try:
    with st.spinner(
        f"Getting regions for {country_name}..."
    ):
        regional_data = get_regions(country_code)

    regions = regional_data["regions"]

    if not regions:
        st.warning(
            "No first-level regions were found for this country."
        )
        st.stop()

except requests.RequestException as exc:
    st.error(
        f"Could not connect to the geographic data service: {exc}"
    )
    st.stop()

except (RuntimeError, KeyError, TypeError) as exc:
    st.error(str(exc))
    st.stop()


try:
    with st.spinner("Checking country news coverage..."):
        country_volume = get_article_volume(
            f'"{country_name}"',
            period
        )

except (requests.RequestException, RuntimeError) as exc:
    country_volume = None
    st.warning(f"Country news count unavailable: {exc}")


col1, col2, col3 = st.columns(3)

col1.metric("Country", country_name)

col2.metric(
    "Matching news articles",
    (
        f"{country_volume:,}"
        if country_volume is not None
        else "Unavailable"
    )
)

col3.metric("Available regions", len(regions))

st.caption(
    "News article counts are not the number of people searching online."
)

st.divider()

st.subheader("Administrative regions")

st.caption(
    f"First-level regions available: {len(regions)}. "
    "The first five are shown below in alphabetical order."
)

if "show_all_regions" not in st.session_state:
    st.session_state["show_all_regions"] = False

if st.button(
    "Show all regions"
    if not st.session_state["show_all_regions"]
    else "Show first five only"
):
    st.session_state["show_all_regions"] = (
        not st.session_state["show_all_regions"]
    )
    st.rerun()

visible_regions = (
    regions
    if st.session_state["show_all_regions"]
    else regions[:5]
)

region_df = pd.DataFrame({
    "Region": visible_regions
})

region_df.insert(
    0,
    "Number",
    range(1, len(region_df) + 1)
)

st.dataframe(
    region_df,
    use_container_width=True,
    hide_index=True
)

with st.expander("Geographic data information"):
    st.write(
        f"Country code: {country_code}"
    )
    st.write(
        f"Boundary year: {regional_data['year']}"
    )
    st.write(
        f"Data source: {regional_data['source']}"
    )
    st.write(
        "Provider: geoBoundaries"
    )

st.divider()

st.subheader("Search within a region")

if st.button("Random region"):
    st.session_state["selected_region"] = random.choice(regions)
    st.rerun()

default_region = st.session_state.get(
    "selected_region",
    regions[0]
)

if default_region not in regions:
    default_region = regions[0]

selected_region = st.selectbox(
    "Choose a region",
    regions,
    index=regions.index(default_region),
    key="selected_region"
)

region_query = (
    f'"{selected_region}" "{country_name}"'
)

st.markdown(f"### {selected_region}")

try:
    with st.spinner("Checking regional news coverage..."):
        region_volume = get_article_volume(
            region_query,
            period
        )

    st.metric(
        "Matching news articles",
        (
            f"{region_volume:,}"
            if region_volume is not None
            else "Unavailable"
        )
    )

except (requests.RequestException, RuntimeError) as exc:
    st.warning(f"Regional article count unavailable: {exc}")


try:
    with st.spinner("Getting news headlines..."):
        articles = get_articles(
            region_query,
            period
        )

    st.markdown("#### Recent headlines")

    if articles.empty:
        st.info("No matching headlines were found.")

    else:
        for _, article in articles.head(10).iterrows():
            headline = article["Headline"]
            publisher = article["Publisher"]
            url = article["URL"]

            if url:
                st.markdown(
                    f"- [{headline}]({url}) — {publisher}"
                )
            else:
                st.markdown(
                    f"- {headline} — {publisher}"
                )

except (requests.RequestException, RuntimeError) as exc:
    st.warning(f"Headlines unavailable: {exc}")


st.divider()

st.subheader("Topic search")

topic = st.text_input(
    "Enter a topic",
    placeholder="Example: artificial intelligence"
)

if st.button(
    "Search topic",
    disabled=not topic.strip()
):
    try:
        with st.spinner("Searching news..."):
            topic_volume = get_article_volume(
                f'"{topic.strip()}"',
                period
            )

        st.metric(
            "Matching news articles",
            (
                f"{topic_volume:,}"
                if topic_volume is not None
                else "Unavailable"
            )
        )

    except (requests.RequestException, RuntimeError) as exc:
        st.error(str(exc))


st.caption(
    "Geographic data: geoBoundaries | News data: GDELT Project"
)
