"""Streamlit 엔트리포인트 — 페이지 라우터 (CLAUDE.md §5.9).

페이지
- views/market.py   : 매크로 유동성 + 기술적 지표 대시보드 (기본)
- views/leverage.py : 레버리지 ETF 신호 (SOXL·TQQQ, 블로그 규칙)

폴더 이름이 pages/ 가 아닌 이유: pages/ 가 있으면 Streamlit 이 옛 자동 멀티페이지를 켜서
/market 같은 주소로 바로 들어올 때 이 라우터(set_page_config·CSS·테마)를 건너뛴다.

★ 어느 페이지도 외부 API를 호출하지 않는다. data/*.parquet 만 읽는다 (설계원칙 1).
"""
from __future__ import annotations

import streamlit as st

from src.viz.ui import inject_css

st.set_page_config(page_title="Market Dashboard", layout="wide",
                   initial_sidebar_state="collapsed")
inject_css()

# 두 페이지가 같이 쓰는 설정. 테마는 차트 팔레트에만 적용된다.
st.sidebar.radio("테마", ["light", "dark"], horizontal=True, key="theme",
                 format_func=lambda m: "라이트" if m == "light" else "다크")

nav = st.navigation(
    # 기본 페이지는 항상 루트(/) 주소다 — url_path 를 주지 않는다
    [st.Page("views/market.py", title="시장 대시보드", icon="📊", default=True),
     st.Page("views/leverage.py", title="레버리지 ETF 신호", icon="📈",
             url_path="leverage")],
    position="top",
)
nav.run()
