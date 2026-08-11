"""Streamlit 엔트리포인트 (CLAUDE.md §5.9).

★ 이 앱은 외부 API를 절대 호출하지 않는다. data/*.parquet 만 읽는다 (설계원칙 1).
   네트워크를 끊어도 정상 동작해야 한다.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st
from plotly.subplots import make_subplots

from config import settings
from src import store
from src.indicators import liquidity as lq
from src.indicators import technical as ta
from src.viz import charts, theme

st.set_page_config(page_title="Market Dashboard", layout="wide",
                   initial_sidebar_state="expanded")


# --------------------------------------------------------------- 데이터 로드
@st.cache_data(ttl=3600)
def load(name: str) -> pd.DataFrame:
    return store.read(name)


@st.cache_data(ttl=3600)
def load_meta() -> dict:
    return store.read_meta()


@st.cache_data(ttl=3600)
def ticker_names() -> dict[str, str]:
    out = {f"KRX.{i['ticker']}": i["name"] for i in settings.series_for("krx_index")}
    out |= {f"YF.{i['ticker']}": i["name"] for i in settings.series_for("yfinance")}
    return out


def close_of(prices: pd.DataFrame, ticker: str) -> pd.Series:
    s = prices[prices["ticker"] == ticker].set_index("date")["close"]
    return s.sort_index().astype("float64")


def net_liquidity(macro: pd.DataFrame) -> pd.Series:
    w = macro.pivot(index="date", columns="series_id", values="value")
    need = ["fred.WALCL", "fred.WTREGEN", "fred.RRPONTSYD"]
    if not all(c in w.columns for c in need):
        return pd.Series(dtype="float64")
    return lq.us_net_liquidity(*[w[c].dropna() for c in need])


def macro_series(macro: pd.DataFrame, sid: str) -> pd.Series:
    s = macro[macro["series_id"] == sid].set_index("date")["value"]
    return s.sort_index().astype("float64")


def clip(s: pd.Series, start: pd.Timestamp) -> pd.Series:
    return s[s.index >= start]


def fmt_delta(s: pd.Series, periods: int = 1, pct: bool = True) -> str | None:
    if len(s) <= periods:
        return None
    prev, cur = s.iloc[-1 - periods], s.iloc[-1]
    if pct:
        return f"{(cur / prev - 1) * 100:+.2f}%" if prev else None
    return f"{cur - prev:+,.2f}"


# --------------------------------------------------------------- 사이드바
prices, macro, flows, meta = load("prices"), load("macro"), load("flows"), load_meta()
names = ticker_names()

st.sidebar.header("설정")

mode = st.sidebar.radio("테마", ["light", "dark"], horizontal=True, key="theme",
                        format_func=lambda m: "라이트" if m == "light" else "다크")

period = st.sidebar.select_slider(
    "기간", options=["6개월", "1년", "3년", "5년", "10년", "전체"], value="3년",
    key="period")
_days = {"6개월": 182, "1년": 365, "3년": 365 * 3,
         "5년": 365 * 5, "10년": 365 * 10}
if period == "전체":
    start = pd.Timestamp("1900-01-01")
else:
    start = pd.Timestamp.today().normalize() - pd.Timedelta(days=_days[period])

st.sidebar.subheader("지표 파라미터")
rsi_n = st.sidebar.slider("RSI 기간", 5, 30, 14, key="rsi_n")
c1, c2 = st.sidebar.columns(2)
macd_fast = c1.number_input("MACD fast", 2, 50, 12, key="macd_fast")
macd_slow = c2.number_input("MACD slow", 3, 100, 26, key="macd_slow")
macd_signal = st.sidebar.number_input("MACD signal", 2, 50, 9, key="macd_signal")
if macd_fast >= macd_slow:
    st.sidebar.error("fast 는 slow 보다 작아야 합니다")
    macd_fast, macd_slow = 12, 26

ma_windows = st.sidebar.multiselect("이동평균", [5, 20, 60, 120, 200],
                                    default=[20, 60, 120])

# --------------------------------------------------------------- 갱신 배지
st.title("매크로 유동성 + 기술적 지표 대시보드")

if not meta:
    st.warning("data/_meta.json 이 없습니다. `python -m src.etl.run_all` 을 먼저 실행하세요.")
else:
    badges = []
    for src, m in sorted(meta.items()):
        icon = "🟢" if m.get("status") == "ok" else "🔴"
        when = str(m.get("last_run", ""))[:16].replace("T", " ")
        tail = f" · ~{m['max_date']}" if m.get("max_date") else ""
        badges.append(f"{icon} **{src}** {when}{tail}")
    st.caption("최종 갱신 &nbsp;|&nbsp; " + " &nbsp;·&nbsp; ".join(badges))
    failed = [s for s, m in meta.items() if m.get("status") != "ok"]
    if failed:
        with st.expander(f"⚠️ 수집 실패 {len(failed)}건", expanded=False):
            for s in failed:
                st.write(f"**{s}** — {meta[s].get('error', '사유 미기록')}")

if prices.empty and macro.empty:
    st.error("데이터가 비어 있습니다. `python -m src.etl.run_all` 을 실행하세요.")
    st.stop()

# --------------------------------------------------------------- KPI
kpi: dict = {}
for tk in ["KRX.1001", "KRX.2001", "YF.^GSPC", "YF.^VIX"]:
    s = close_of(prices, tk)
    if not s.empty:
        kpi[names.get(tk, tk)] = (f"{s.iloc[-1]:,.2f}", fmt_delta(s))

nl = net_liquidity(macro)
if not nl.empty:
    kpi["미국 순유동성"] = (f"{nl.iloc[-1]:,.0f}B$", fmt_delta(nl, pct=False))

charts.kpi_row(kpi)
st.divider()

# --------------------------------------------------------------- 탭
tab_ov, tab_kr, tab_liq, tab_x = st.tabs(
    ["개요", "한국 시장", "유동성", "크로스에셋"])

with tab_ov:
    avail = [t for t in names if not close_of(prices, t).empty]
    if not avail:
        st.info("prices.parquet 이 비어 있습니다.")
    else:
        pick = st.selectbox("지수", avail, format_func=lambda t: f"{names[t]} ({t})")
        s = clip(close_of(prices, pick), start)
        st.plotly_chart(
            charts.price_macd_rsi(
                s.to_frame("close"), f"{names[pick]} — 가격 · MACD · RSI",
                ma=tuple(ma_windows), rsi_n=rsi_n, fast=int(macd_fast),
                slow=int(macd_slow), signal=int(macd_signal), mode=mode,
                color_key=pick),
            width="stretch")

        with st.expander("데이터 보기 (표)"):
            tbl = s.to_frame("종가")
            tbl[f"RSI({rsi_n})"] = ta.rsi(s, rsi_n)
            tbl["이격도(20)"] = ta.disparity(s, 20)
            st.dataframe(tbl.tail(250).iloc[::-1], width="stretch")

with tab_kr:
    kr = [t for t in ["KRX.1001", "KRX.2001", "KRX.1028"]
          if not close_of(prices, t).empty]
    if not kr:
        st.info("한국 지수 데이터가 없습니다.")
    else:
        pick = st.selectbox("시장", kr, format_func=lambda t: names[t], key="kr")
        s = clip(close_of(prices, pick), start)
        st.plotly_chart(charts.disparity_bands(s, mode=mode,
                                               title=f"{names[pick]} 이격도"),
                        width="stretch")

    st.subheader("한국 유동성")
    dep = clip(macro_series(macro, "ecos.investor_deposit"), start)
    mcap = clip(macro_series(macro, "ecos.kospi_marcap"), start)
    tval = clip(macro_series(macro, "ecos.kospi_value"), start)

    if dep.empty:
        st.info("예탁금 데이터가 없습니다. `python -m src.etl.run_all --only ecos`")
    else:
        import plotly.graph_objects as go
        p = theme.palette(mode)
        # 예탁금은 월간, 시총·거래대금은 일간 → 월말로 맞춰 비교한다
        mcap_m = mcap.resample("ME").last()
        tval_m = tval.resample("ME").mean()
        ratio = lq.deposit_ratio(dep, mcap_m)
        turn = lq.deposit_turnover(tval_m, dep)

        f = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.07)
        f.add_trace(go.Scatter(x=dep.index, y=dep, name="예탁금", mode="lines",
                               line=dict(color=p["series"][0], width=2),
                               hovertemplate="%{y:,.1f}조<extra>예탁금</extra>"),
                    row=1, col=1)
        f.add_trace(go.Scatter(x=ratio.index, y=ratio, name="예탁금/시총", mode="lines",
                               line=dict(color=p["series"][1], width=2),
                               hovertemplate="%{y:.2f}%<extra>예탁금/시총</extra>"),
                    row=2, col=1)
        f.update_yaxes(title_text="예탁금 (조원)", row=1, col=1)
        f.update_yaxes(title_text="예탁금/시총 (%)", row=2, col=1)
        f.update_layout(title="투자자 예탁금 (월간)")
        st.plotly_chart(theme.apply(f, mode, height=520), width="stretch")

        m1, m2_, m3 = st.columns(3)
        m1.metric("예탁금", f"{dep.iloc[-1]:,.1f}조원", fmt_delta(dep))
        if not ratio.empty:
            m2_.metric("예탁금/시총", f"{ratio.iloc[-1]:.2f}%", fmt_delta(ratio))
        if not turn.empty:
            m3.metric("예탁금 회전율", f"{turn.iloc[-1]:.3f}", fmt_delta(turn))
        st.caption("예탁금은 ECOS 증시주변자금동향(901Y056) 월간 계열입니다. "
                   "시총·거래대금은 일간이라 월말/월평균으로 맞춰 계산했습니다.")

    st.subheader("투자자별 수급")
    fk = clip(macro_series(macro, "ecos.foreign_net_kospi"), start)
    if not fk.empty:
        import plotly.graph_objects as go
        p = theme.palette(mode)
        fq = fk.resample("W").sum()
        pos, neg = p["series"][0], p["series"][7]
        f = go.Figure(go.Bar(
            x=fq.index, y=fq, name="외국인 순매수",
            marker=dict(color=[pos if v >= 0 else neg for v in fq],
                        line=dict(width=0)),
            hovertemplate="%{y:+,.2f}조<extra>주간 합계</extra>"))
        f.add_hline(y=0, line=dict(color=p["axis"], width=1))
        f.update_layout(title="외국인 순매수 — 유가증권시장 (주간 합계, 조원)",
                        showlegend=False)
        st.plotly_chart(theme.apply(f, mode, height=400), width="stretch")

    if flows.empty:
        st.info(
            "개인/기관 구분은 `flows.parquet` 이 필요하고, 이건 data.krx.co.kr "
            "계정(`KRX_ID`/`KRX_PW`)이 있어야 받습니다. 위 차트는 ECOS에서 오는 "
            "외국인 수급만 표시합니다. docs/SETUP_KEYS.md 참고."
        )
    else:
        mk = st.radio("시장", sorted(flows["market"].unique()), horizontal=True,
                      key="flow_market")
        st.plotly_chart(
            charts.investor_flow_bar(flows[flows["date"] >= start], mk, mode=mode),
            width="stretch")

with tab_liq:
    if nl.empty:
        st.info("FRED 유동성 3종(WALCL/WTREGEN/RRPONTSYD)이 없습니다.")
    else:
        scale = st.radio("표현 방식", ["stacked", "indexed", "dual_axis"],
                         horizontal=True, key="liq_scale",
                         format_func=lambda s: {"stacked": "상하 분할 (권장)",
                                                "indexed": "지수화",
                                                "dual_axis": "이중축"}[s])
        cmp_t = st.selectbox("비교 지수", [t for t in names
                                        if not close_of(prices, t).empty],
                             format_func=lambda t: names[t], key="liq")
        st.plotly_chart(
            charts.liquidity_overlay(clip(nl, start),
                                     clip(close_of(prices, cmp_t), start),
                                     f"미국 순유동성 vs {names[cmp_t]}",
                                     mode=mode, scale=scale),
            width="stretch")
        st.caption("순유동성 = 연준 총자산 − 재무부 일반계정(TGA) − 역레포(ON RRP), "
                   "수요일 기준 주간 정렬. 단위 십억 USD.")

    others = {"fred.DFF": "연방기금 실효금리", "fred.T10Y2Y": "미 10Y-2Y",
              "fred.BAMLH0A0HYM2": "하이일드 OAS (3년치만 공개)",
              "fred.M2SL": "미국 M2",
              "ecos.base_rate": "한국은행 기준금리", "ecos.m2": "한국 M2(평잔)",
              "ecos.usdkrw": "원/달러 환율"}
    have = {k: v for k, v in others.items() if not macro_series(macro, k).empty}
    if have:
        pick = st.selectbox("기타 매크로", list(have), format_func=lambda k: have[k])
        s = clip(macro_series(macro, pick), start)
        import plotly.graph_objects as go
        p = theme.palette(mode)
        f = go.Figure(go.Scatter(x=s.index, y=s, mode="lines", name=have[pick],
                                 line=dict(color=p["series"][0], width=2)))
        f.update_layout(title=have[pick], showlegend=False)
        st.plotly_chart(theme.apply(f, mode, height=360), width="stretch")

with tab_x:
    avail = [t for t in names if not close_of(prices, t).empty]
    picks = st.multiselect("비교 (시작=100 지수화)", avail,
                           default=[t for t in ["KRX.1001", "YF.^GSPC"]
                                    if t in avail],
                           format_func=lambda t: names[t])
    if len(picks) < 2:
        st.info("2개 이상 선택하세요.")
    else:
        import plotly.graph_objects as go
        f = go.Figure()
        for t in picks:
            s = clip(close_of(prices, t), start)
            if s.empty:
                continue
            f.add_trace(go.Scatter(x=s.index, y=s / s.iloc[0] * 100,
                                   name=names[t], mode="lines",
                                   line=dict(color=theme.series_color(t, mode),
                                             width=2),
                                   hovertemplate="%{y:,.1f}<extra>"
                                                 + names[t] + "</extra>"))
        f.add_hline(y=100, line=dict(color=theme.palette(mode)["axis"], width=1))
        f.update_layout(title="상대 성과 (시작 = 100)")
        st.plotly_chart(theme.apply(f, mode, height=460), width="stretch")

        corr = pd.DataFrame({names[t]: clip(close_of(prices, t), start)
                             for t in picks}).pct_change(fill_method=None).corr()
        st.subheader("일간 수익률 상관")
        st.dataframe(corr.style.format("{:.2f}"), width="stretch")
