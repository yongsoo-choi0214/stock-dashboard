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
from src.research import ic as ic_mod
from src.research import regime
from src.research import snapshot as snapshot_mod
from src.research import vulnerability as vu
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


@st.cache_data(ttl=3600)
def vulnerability_result() -> dict:
    """취약성 지수 + walk-forward 검증 결과. 계산이 무거워 캐시한다."""
    macro_, prices_, flows_ = store.read("macro"), store.read("prices"), store.read("flows")
    if macro_.empty or prices_.empty:
        return {}

    def ms(sid: str) -> pd.Series:
        s = macro_[macro_["series_id"] == sid].set_index("date")["value"]
        return s.sort_index().astype("float64")

    close = prices_[prices_["ticker"] == "KRX.1001"].set_index("date")["close"]
    close = close.sort_index().astype("float64")
    if close.empty:
        return {}

    foreign = None
    if not flows_.empty:
        sel = flows_[(flows_["market"] == "KOSPI") &
                     (flows_["investor"] == "외국인합계")]
        if not sel.empty:
            foreign = sel.set_index("date")["net_value"].sort_index() / 1e12

    need = ["fred.WALCL", "fred.WTREGEN", "fred.RRPONTSYD"]
    netliq = (lq.us_net_liquidity(*[ms(s) for s in need])
              if all(not ms(s).empty for s in need) else None)

    def opt(sid: str) -> pd.Series | None:
        # Series 는 `or` 로 기본값을 줄 수 없다 (진리값이 모호)
        s = ms(sid)
        return None if s.empty else s

    def diff(a: str, b: str) -> pd.Series | None:
        x, y = ms(a), ms(b)
        return None if x.empty or y.empty else (x - y).dropna()

    comp = vu.build_components(
        close, turnover=opt("ecos.kospi_value"),
        foreign_flow=foreign,
        market_cap=opt("ecos.kospi_marcap"),
        net_liquidity=netliq,
        yield_curve=diff("ecos.ktb10y", "ecos.ktb3y"),
        exports=opt("ecos.exports"),
        margin_debt=opt("ecos.margin_debt"),
        pbr=opt("krx.1001_pbr"),
        credit_spread=diff("ecos.corp_aa", "ecos.ktb3y"))
    if comp.dropna(how="all").empty:
        return {}
    res = vu.walk_forward(comp, close, horizon=60, split="2016-01-01",
                          condition=vu.near_high(close))

    # 시점(point-in-time) 검증 — 순유동성을 '당시 알 수 있었던 값'으로 바꿔
    # 같은 구간·같은 분할로 재평가한다. 유출이 얼마였는지 그 차이가 말해준다.
    res["pit"] = _pit_comparison(close, comp, foreign, opt)
    return res


def _pit_comparison(close, comp_now, foreign, opt) -> dict:
    from src.etl.alfred import point_in_time

    vint = store.read("vintages")
    need = ["fred.WALCL", "fred.WTREGEN", "fred.RRPONTSYD"]
    if vint.empty:
        return {}
    pit = {s: point_in_time(vint, s) for s in need}
    if any(v.empty for v in pit.values()):
        return {}

    comp_pit = vu.build_components(
        close, turnover=opt("ecos.kospi_value"), foreign_flow=foreign,
        market_cap=opt("ecos.kospi_marcap"),
        net_liquidity=lq.us_net_liquidity(*[pit[s] for s in need]))

    common = comp_now.dropna().index.intersection(comp_pit.dropna().index)
    if len(common) < 500:
        return {}
    mask = pd.Series(False, index=close.index)
    mask.loc[common] = True
    mask &= vu.near_high(close).reindex(close.index).fillna(False)

    # 분할점은 공통 구간 '안쪽'이어야 한다. 밖에 두면 표본 내가 비어
    # 방향이 기본값으로 떨어지고 비교가 성립하지 않는다.
    split = "2021-01-01"
    return {
        "period": (common.min(), common.max()),
        "split": split,
        "now": vu.walk_forward(comp_now, close, split=split, condition=mask),
        "pit": vu.walk_forward(comp_pit, close, split=split, condition=mask),
    }


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
tab_ov, tab_kr, tab_liq, tab_x, tab_res = st.tabs(
    ["개요", "한국 시장", "유동성", "크로스에셋", "연구"])

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
        fc1, fc2 = st.columns([2, 3])
        mk = fc1.radio("시장", sorted(flows["market"].unique()), horizontal=True,
                       key="flow_market")
        agg = fc2.radio("집계", ["W", "ME", None], horizontal=True, key="flow_freq",
                        format_func=lambda f: {"W": "주간", "ME": "월간",
                                               None: "일간"}[f])
        st.plotly_chart(
            charts.investor_flow_bar(flows[flows["date"] >= start], mk,
                                     mode=mode, freq=agg),
            width="stretch")
        st.caption("순매수 대금(원 단위 수집 → 억원 표시). "
                   "네 구분의 합은 항상 0입니다 — 한쪽이 사면 다른 쪽이 팝니다.")

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

with tab_res:
    st.caption(
        "지표가 실제로 앞날을 설명하는지 재는 화면입니다. "
        "IC(Information Coefficient)는 신호와 이후 수익률의 순위상관이며, "
        "**절대값이 아니라 부호의 안정성**을 봐야 합니다."
    )

    base_t = st.selectbox("대상 지수", [t for t in names
                                     if not close_of(prices, t).empty],
                          format_func=lambda t: names[t], key="res_ticker")
    px = clip(close_of(prices, base_t), start)

    if len(px) < 300:
        st.info("표본이 부족합니다. 사이드바에서 기간을 늘리세요.")
    else:
        signals: dict[str, pd.Series] = {
            f"RSI({rsi_n})": ta.rsi(px, rsi_n),
            "이격도(20)": ta.disparity(px, 20),
        }
        if not nl.empty:
            signals["순유동성 4주Δ"] = nl.diff(4)
        for sid, label in [("ecos.investor_deposit", "예탁금"),
                           ("ecos.usdkrw", "원/달러"),
                           ("ecos.m2", "한국 M2"),
                           ("ecos.foreign_net_kospi", "외국인순매수 20일합")]:
            s = macro_series(macro, sid)
            if s.empty:
                continue
            signals[label] = s.rolling(20).sum() if "순매수" in label else s

        use_change = st.checkbox(
            "레벨 대신 20일 변화량으로 평가", value=False,
            help="추세가 있는 레벨 계열(환율·M2·예탁금)은 가격과 추세만 같아도 "
                 "IC가 크게 나옵니다. 변화량으로 바꾸면 그 허수가 사라집니다.")
        if use_change:
            signals = {f"Δ{k}": v.diff(20) for k, v in signals.items()}

        lags = {k: ic_mod.lag_for("ecos.m2") for k in signals if "M2" in k}
        with st.spinner("IC 계산 중…"):
            tbl = ic_mod.ic_table(signals, px, horizons=(5, 20, 60), lags=lags)
        st.dataframe(tbl.style.format({c: "{:.3f}" for c in tbl.columns
                                       if c != "n"}), width="stretch")
        st.caption("hit_Nd = 신호 상위 절반 구간에서 N일 뒤 상승했던 비율. "
                   "lag_days = series.yaml 의 발표 시차 (§7.2 look-ahead 방지).")

        pick_sig = st.selectbox("롤링 IC 로 볼 신호", list(signals), key="res_sig")
        horizon = st.radio("예측 기간", [5, 20, 60], index=1, horizontal=True,
                           key="res_h", format_func=lambda h: f"{h}일")
        with st.spinner("롤링 IC 계산 중…"):
            r_ic = ic_mod.rolling_ic(signals[pick_sig], px, horizon, window=252,
                                     lag_days=lags.get(pick_sig, 0))
        if r_ic.dropna().empty:
            st.info("롤링 IC 를 낼 표본이 부족합니다.")
        else:
            import plotly.graph_objects as go
            pal = theme.palette(mode)
            f = go.Figure(go.Scatter(x=r_ic.index, y=r_ic, mode="lines",
                                     name="IC",
                                     line=dict(color=pal["series"][0], width=2),
                                     hovertemplate="%{y:.3f}<extra>IC</extra>"))
            f.add_hline(y=0, line=dict(color=pal["axis"], width=1))
            f.update_yaxes(range=[-1, 1], title_text="IC")
            f.update_layout(title=f"{pick_sig} — {horizon}일 예측 IC (252일 롤링)",
                            showlegend=False)
            st.plotly_chart(theme.apply(f, mode, height=380), width="stretch")
            st.caption(f"평균 {r_ic.mean():.3f} · 부호 유지 비율 "
                       f"{max((r_ic > 0).mean(), (r_ic < 0).mean()):.1%}")

    st.divider()
    st.subheader("유동성 × 모멘텀 레짐")
    if nl.empty:
        st.info("FRED 유동성 데이터가 없어 레짐을 만들 수 없습니다.")
    else:
        reg = regime.classify(nl, close_of(prices, base_t))
        if reg.empty:
            st.info("레짐을 만들 표본이 부족합니다.")
        else:
            cur = regime.current(reg)
            c1, c2, c3 = st.columns(3)
            c1.metric("현재 레짐", cur["regime"], f"{cur['streak_days']}일 연속")
            c2.metric("유동성 Δ 백분위", f"{cur['liq_pr']:.0%}")
            c3.metric("모멘텀 백분위", f"{cur['mom_pr']:.0%}")

            st.dataframe(regime.summarize(reg, close_of(prices, base_t), 20),
                         width="stretch")
            st.caption(
                "유동성 4주 변화량과 60일 모멘텀을 각각 252일 롤링 백분위 50% "
                "기준으로 잘라 4분면으로 나눕니다. "
                "**서술 통계이지 전략 성과가 아닙니다** — 표본이 겹치고 "
                "레짐 판정에 모멘텀이 이미 들어가 있습니다."
            )

    st.divider()
    st.subheader("취약성 지수 — 조정 위험 게이지")

    vres = vulnerability_result()
    if not vres:
        st.info("취약성 지수를 만들 데이터가 부족합니다.")
    else:
        vidx, vtgt = vres["index"], vres["target"]
        kospi_all = close_of(prices, "KRX.1001")
        nh = vu.near_high(kospi_all)
        cur_v = vidx.dropna()
        applicable = bool(nh.iloc[-1]) if len(nh) else False

        c1, c2, c3 = st.columns(3)
        c1.metric("현재 취약성", f"{cur_v.iloc[-1]:.2f}" if not cur_v.empty else "—",
                  help="0~1. 높을수록 취약. 5개 컴포넌트의 롤링 백분위 평균(20일 평활)")
        c2.metric("현재 낙폭", f"{vu.drawdown(kospi_all).iloc[-1]:.1%}")
        c3.metric("지수 적용 가능?", "예" if applicable else "아니오 — 이미 조정 중",
                  help="고점 대비 -10% 이내일 때만 해석합니다")

        if not applicable:
            st.warning(
                "지금은 이미 조정이 진행 중이라 이 지수를 '앞으로 빠질까'로 "
                "읽으면 안 됩니다. 고점 대비 -10% 이내로 회복한 뒤부터 유효합니다."
            )

        ep = vu.episodes(kospi_all)
        ep_shown = ep[ep["peak"] >= cur_v.index.min()] if not cur_v.empty else ep
        st.plotly_chart(
            charts.vulnerability_panel(cur_v, kospi_all[kospi_all.index >= cur_v.index.min()],
                                       ep_shown, mode=mode, applicable=nh),
            width="stretch")

        st.markdown("**검증 — 전반부에서 방향만 정하고 후반부(2016~)에서 평가**")
        m1, m2 = st.columns(2)
        m1.metric("IC (표본 내, ~2015)", f"{vres['ic_is']:+.3f}")
        m2.metric("IC (표본 외, 2016~)", f"{vres['ic_oos']:+.3f}",
                  help="두 값이 비슷해야 과적합이 아닙니다")
        st.dataframe(vres["deciles_oos"], width="stretch")
        st.caption(
            "취약성 5분위별 **향후 60영업일 최대낙폭** (고점 근처 구간만). "
            "학습 파라미터는 0개 — 컴포넌트를 롤링 백분위로 정규화해 평균낼 뿐이고, "
            "각 컴포넌트의 방향만 2015년까지 데이터로 정했습니다. "
            "조정은 21.6년간 8회뿐이라 이걸 '예측'이 아니라 **위험 게이지**로 봐야 합니다."
        )

        with st.expander("컴포넌트별 기여"):
            st.dataframe(pd.DataFrame({
                "표본내 IC": pd.Series(vres["is_ic"]),
                "취약 방향": pd.Series(vres["orient"]).map(
                    {1: "값이 클수록 취약", -1: "값이 작을수록 취약"}),
            }).round(3), width="stretch")
            st.caption("IC 는 향후 최대낙폭과의 순위상관. 낙폭은 음수라 "
                       "IC>0 이면 '값이 클수록 낙폭이 얕다' → 취약 방향은 반대입니다.")

        pit = vres.get("pit") or {}
        if pit:
            st.markdown("**시점(point-in-time) 검증 — 유출을 걷어내면 얼마나 떨어지나**")
            a, b = pit["now"], pit["pit"]
            st.dataframe(pd.DataFrame({
                "IC (표본 내)": [a["ic_is"], b["ic_is"]],
                "IC (표본 외)": [a["ic_oos"], b["ic_oos"]],
            }, index=["현재판 (정정 반영된 최신값)",
                      "시점판 (최초 발표치 + 공표일)"]).round(3),
                width="stretch")
            drop = abs(b["ic_oos"]) / abs(a["ic_oos"]) - 1 if a["ic_oos"] else 0
            st.caption(
                f"{pit['period'][0].date()} ~ {pit['period'][1].date()}, "
                f"분할 {pit['split']}. **표본 외 성능 {drop:+.0%}.** "
                "FRED 값은 나중에 정정되고 하루 뒤 공표되는데, 지금까지의 지표는 "
                "'오늘 시점에서 본 과거'를 썼습니다. ALFRED 아카이브로 "
                "'당시 실제로 알 수 있었던 값'을 넣어 다시 재면 성능이 이만큼 "
                "내려갑니다. **이쪽이 진짜 숫자입니다.** "
                "운영 지수는 히스토리가 긴 현재판을 쓰되(최근 값은 두 판이 동일), "
                "성능은 이 숫자로 판단하세요."
            )

        st.markdown("**실전 검증 — 8번의 조정 고점에서 지수가 얼마였나**")
        ev = vu.event_study(vidx, kospi_all)
        st.dataframe(ev, width="stretch", hide_index=True)
        n_hit = int((ev["판정"] == "경보").sum())
        n_warn = int((ev["판정"] == "주의").sum())
        st.caption(
            f"경보 {n_hit}회 · 주의 {n_warn}회 · 무신호 {len(ev) - n_hit - n_warn}회. "
            "**위 5분위 표보다 이쪽이 더 정직한 화면입니다.** 조건부 분포는 "
            "뚜렷하게 갈리지만, '그 8번을 실제로 짚었나'는 다른 질문이고 절반쯤 "
            "놓칩니다. 8번을 다 맞혔다면 오히려 미래 정보가 샜다고 의심해야 합니다. "
            "확률을 기울이는 게이지로 쓰고, 매매 신호로 쓰지 마세요."
        )

        with st.expander("조정 이력 (-15% 이상)"):
            show = ep.copy()
            show["depth"] = (show["depth"] * 100).round(1)
            show["기간(일)"] = (show["trough"] - show["peak"]).dt.days
            st.dataframe(show.rename(columns={"peak": "고점", "trough": "저점",
                                              "end": "회복", "depth": "낙폭%"}),
                         width="stretch")

    st.divider()
    st.subheader("기록 이력 — 그날 실제로 뭐라고 했나")
    snaps = load("snapshots")
    if snaps.empty:
        st.info("아직 기록이 없습니다. ETL 이 매 실행마다 그날 값을 남깁니다.")
    else:
        wide = snaps.pivot(index="date", columns="metric", values="value")
        st.dataframe(wide.sort_index(ascending=False).round(3), width="stretch")
        st.caption(
            f"{len(wide)}일치 기록. **한번 기록한 날짜는 고치지 않습니다.** "
            "대시보드의 다른 값은 매번 전체 히스토리에서 다시 계산되므로, "
            "코드나 방법론이 바뀌면 과거 판단도 함께 바뀝니다 — 실제로 이 프로젝트에서 "
            "ALFRED 시점 데이터를 넣자 같은 날짜의 성능 수치가 -0.468 에서 -0.304 로 "
            "움직였습니다. 이 표만이 '그때 정말 뭐라고 했는지'를 남깁니다."
        )
        if "vulnerability" in wide.columns and vres:
            diff = snapshot_mod.compare("vulnerability", vres["index"])
            if not diff.empty and diff["차이"].abs().max() > 1e-6:
                st.warning(
                    f"기록값과 재계산값이 최대 {diff['차이'].abs().max():.3f} "
                    "차이납니다 — 그 사이 방법론이 바뀌었다는 뜻입니다.")
