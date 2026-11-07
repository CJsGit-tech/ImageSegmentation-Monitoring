"""Server entry point: the Streamlit app plus the route that manages the login cookie."""

import streamlit as st
from starlette.routing import Route

from core.cookie_store import ROUTE_PATH, session_route

app = st.App("app.py", routes=[Route(ROUTE_PATH, session_route, methods=["POST"])])
