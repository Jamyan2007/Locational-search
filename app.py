
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
                            "Show top 5" if st.session_state.show_ten
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
