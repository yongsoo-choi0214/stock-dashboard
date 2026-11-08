"""Streamlit 공용 헬퍼 — 페이지들이 함께 쓰는 로더·포맷·상태 표시.

★ 여기서도 외부 API 는 호출하지 않는다(설계원칙 1). data/*.parquet 만 읽는다.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from config import settings
from src import store


def _stamp(path) -> float:
    """파일 수정 시각. 캐시 키에 넣어 Actions 가 data/ 를 커밋하면 즉시 다시 읽게 한다.
    ttl 만 쓰면 장중 30분 갱신이 화면에 최대 1시간 늦게 반영된다."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def data_stamp() -> float:
    return _stamp(settings.DATA_DIR / "prices.parquet")


@st.cache_data(ttl=3600)
def _load(name: str, stamp: float) -> pd.DataFrame:
    return store.read(name)


def load(name: str) -> pd.DataFrame:
    return _load(name, _stamp(settings.DATA_DIR / f"{name}.parquet"))


@st.cache_data(ttl=3600)
def _load_meta(stamp: float) -> dict:
    return store.read_meta()


def load_meta() -> dict:
    return _load_meta(_stamp(settings.META_JSON))


@st.cache_data(ttl=3600)
def ticker_names() -> dict[str, str]:
    out = {f"KRX.{i['ticker']}": i["name"] for i in settings.series_for("krx_index")}
    out |= {f"YF.{i['ticker']}": i["name"] for i in settings.series_for("yfinance")}
    return out


def close_of(prices: pd.DataFrame, ticker: str) -> pd.Series:
    s = prices[prices["ticker"] == ticker].set_index("date")["close"]
    return s.sort_index().astype("float64")


def ohlcv_of(prices: pd.DataFrame, ticker: str) -> pd.DataFrame:
    d = prices[prices["ticker"] == ticker].set_index("date").sort_index()
    return d[["open", "high", "low", "close", "volume"]].astype("float64")


def fmt_delta(s: pd.Series, periods: int = 1, pct: bool = True) -> str | None:
    if len(s) <= periods:
        return None
    prev, cur = s.iloc[-1 - periods], s.iloc[-1]
    if pct:
        return f"{(cur / prev - 1) * 100:+.2f}%" if prev else None
    return f"{cur - prev:+,.2f}"


def mode() -> str:
    """사이드바 테마 선택(라우터가 렌더). 차트 팔레트에만 쓰인다."""
    return st.session_state.get("theme", "light")


def freshness(meta: dict, sources: list[str] | None = None) -> None:
    """갱신 상태 한 줄 + 소스별 상세는 팝오버로 접는다.

    '데이터가 언제 갱신됐는지 모르는 대시보드는 신뢰할 수 없다'(§3.4) —
    정보는 그대로 두되 화면을 덮지 않게 한다. 실패가 있으면 펼쳐서 보여준다.
    """
    if not meta:
        st.warning("data/_meta.json 이 없습니다. `python -m src.etl.run_all` 을 먼저 실행하세요.")
        return
    items = {k: v for k, v in meta.items() if sources is None or k in sources}
    failed = [k for k, v in items.items() if v.get("status") != "ok"]
    last = max((str(v.get("last_run", "")) for v in items.values()), default="")
    maxd = max((str(v.get("max_date", "")) for v in items.values() if v.get("max_date")),
               default="")
    icon = "🔴" if failed else "🟢"
    head = (f"{icon} 최종 갱신 {last[:16].replace('T', ' ')} KST · 데이터 ~{maxd} · "
            f"{len(items) - len(failed)}/{len(items)}개 소스 정상")
    c1, c2 = st.columns([6, 1], vertical_alignment="center")
    c1.caption(head)
    with c2.popover("소스별", width="stretch"):
        for src, m in sorted(items.items()):
            ok = "🟢" if m.get("status") == "ok" else "🔴"
            when = str(m.get("last_run", ""))[:16].replace("T", " ")
            tail = f" · ~{m['max_date']}" if m.get("max_date") else ""
            st.markdown(f"{ok} **{src}** {when}{tail}")
    if failed:
        with st.expander(f"⚠️ 수집 실패 {len(failed)}건", expanded=True):
            for s in failed:
                st.write(f"**{s}** — {items[s].get('error', '사유 미기록')}")


CSS = """
<style>
/* 본문 폭·여백 — 기본값은 상하 여백이 커서 첫 화면에 정보가 적게 들어온다 */
.block-container {padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1400px;}
h1 {font-size: 1.75rem !important; font-weight: 700 !important; letter-spacing: -0.01em;}
h2, h3 {letter-spacing: -0.01em;}
[data-testid="stMetricValue"] {font-size: 1.55rem;}
[data-testid="stMetricLabel"] p {font-size: 0.82rem; opacity: .75;}
div[data-testid="stExpander"] details summary p {font-weight: 600;}
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)
