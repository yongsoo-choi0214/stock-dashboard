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
from src.research import breadth
from src.research import flows_view as fv
from src.research import ic as ic_mod
from src.research import leverage as lev
from src.research import regime
from src.research import seasonality as sea
from src.research import catalog as catalog_mod
from src.research import snapshot as snapshot_mod
from src.research import summary
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
    """취약성 지수 + walk-forward 검증 결과. 계산이 무거워 캐시한다.

    ★ 예외를 밖으로 던지지 않는다. 이 함수는 상단 요약과 연구 탭 양쪽에서
    불리는데, 여기서 터지면 **대시보드 전체가 빈 화면**이 된다. 실제로 배포
    환경에서 그렇게 됐다 — 패널 하나의 실패가 전부를 죽이면 안 된다
    (설계원칙 5 를 뷰 레이어에도 적용).
    실패는 삼키지 말고 error 로 담아 화면에 드러낸다.
    """
    try:
        return _vulnerability_result()
    except Exception as e:
        import traceback
        return {"error": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc()}


def _vulnerability_result() -> dict:
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

# --------------------------------------------------------------- 오늘 요약
_kospi = close_of(prices, "KRX.1001")
if not _kospi.empty:
    _reg = regime.classify(nl, _kospi) if not nl.empty else pd.DataFrame()
    _cur = regime.current(_reg)
    _vres = vulnerability_result()
    if _vres.get("error"):
        _vres = {}          # 요약은 취약성 없이도 나머지를 보여준다
    _ff = flows[(flows["market"] == "KOSPI") &
                (flows["investor"] == "외국인합계")].set_index("date")["net_value"]
    _aa, _k3, _k10 = (macro_series(macro, f"ecos.{x}")
                      for x in ("corp_aa", "ktb3y", "ktb10y"))
    rows = summary.build(
        close=_kospi, drawdown=vu.drawdown(_kospi),
        vulnerability=_vres.get("index") if _vres else None,
        applicable=bool(vu.near_high(_kospi).iloc[-1]),
        regime=_cur.get("regime"), regime_days=_cur.get("streak_days"),
        foreign_flow=_ff.sort_index() if not _ff.empty else None,
        credit_spread=(_aa - _k3).dropna() if not _aa.empty else None,
        yield_curve=(_k10 - _k3).dropna() if not _k10.empty else None,
        bsi=macro_series(macro, "ecos.bsi"), meta=meta)
    if rows:
        icon = {"ok": "🟢", "warn": "🟡", "bad": "🔴", "info": "⚪"}
        st.markdown(
            "**오늘** &nbsp; "
            + " &nbsp;·&nbsp; ".join(f"{icon.get(l, '•')} {lab} **{v}**"
                                    for l, lab, v in rows))

st.divider()

# --------------------------------------------------------------- 탭
tab_ov, tab_kr, tab_liq, tab_x, tab_res, tab_lev, tab_data = st.tabs(
    ["개요", "한국 시장", "유동성", "크로스에셋", "연구", "신용·수급", "데이터"])

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

        # --- 밸류에이션 --------------------------------------------------
        code = pick.split(".")[1]
        per = macro_series(macro, f"krx.{code}_per")
        pbr = macro_series(macro, f"krx.{code}_pbr")
        dvy = macro_series(macro, f"krx.{code}_divyield")
        mcap = macro_series(macro, f"krx.{code}_marcap")

        if per.empty and pbr.empty:
            st.info("밸류에이션 데이터가 없습니다. "
                    "`python -m src.etl.run_all --only krx_fundamental`")
        else:
            st.subheader("밸류에이션")
            v1, v2, v3, v4 = st.columns(4)
            for col, s_, label, fmt in [(v1, per, "PER", "{:.2f}"),
                                        (v2, pbr, "PBR", "{:.2f}"),
                                        (v3, dvy, "배당수익률", "{:.2f}%"),
                                        (v4, mcap, "시가총액", "{:,.0f}조원")]:
                if s_.empty:
                    continue
                pr = ta.pct_rank(s_.rename("x"), 252).dropna()
                col.metric(label, fmt.format(s_.iloc[-1]),
                           f"{pr.iloc[-1]:.0%} 분위" if not pr.empty else None,
                           delta_color="off")

            metric = st.radio("지표", ["PER", "PBR", "배당수익률"],
                              horizontal=True, key="valuation_pick")
            chosen = {"PER": per, "PBR": pbr, "배당수익률": dvy}[metric]
            if chosen.empty:
                st.info(f"{metric} 데이터가 없습니다.")
            else:
                st.plotly_chart(
                    charts.level_with_percentile(
                        clip(chosen, start), f"{names[pick]} {metric}",
                        mode=mode, ylabel=metric),
                    width="stretch")
                st.caption(
                    "하단은 252일 롤링 백분위입니다. **레벨만 보면 비싼지 알 수 없습니다** — "
                    f"{metric} 값 자체보다 '그 시장의 역사에서 지금이 어디쯤인가'가 "
                    "판단에 쓰입니다. PBR 은 취약성 지수의 컴포넌트이기도 합니다."
                )

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

    st.subheader("시장 폭 (breadth)")
    _sect = [f"KRX.{i['ticker']}" for i in settings.series_for("krx_sector")
             if i.get("group") == "sector"]
    _mat = breadth.sector_matrix(prices, _sect)
    if _mat.empty or _mat.shape[1] < 10:
        st.info("업종지수가 부족합니다. `run_all --only krx_sector`")
    else:
        pa = breadth.pct_above_ma(_mat, 200)
        ar = breadth.advance_ratio(_mat, 20)
        sl = breadth.small_vs_large(prices, 60)
        dv = breadth.divergence(close_of(prices, "KRX.1001"), pa)
        b1, b2, b3, b4 = st.columns(4)
        b1.metric("200일선 위 업종", f"{pa.iloc[-1]:.0%}",
                  f"{_mat.shape[1]}개 업종 중", delta_color="off")
        b2.metric("20일 상승 업종", f"{ar.iloc[-1]:.0%}")
        if not sl.empty:
            b3.metric("소형-대형 60일", f"{sl.iloc[-1]:+.1%}",
                      "음수면 대형주만 강세", delta_color="off")
        if not dv.empty:
            b4.metric("지수-폭 괴리", f"{dv.iloc[-1]:+.2f}",
                      "+1 이면 지수만 신고가", delta_color="off")

        st.plotly_chart(
            charts.macro_lines({"200일선 위 업종비율": clip(pa, start),
                                "20일 상승 업종비율": clip(ar, start)},
                               "시장 폭 (업종 기준)", mode=mode,
                               ylabel="비율", height=380,
                               hover_fmt=":.0%"),
            width="stretch")
        st.caption(
            "지수는 시가총액 가중이라 대형주 몇 개가 끌어올리면 신고가가 납니다. "
            "그동안 나머지가 무너져도 지수에는 안 보입니다 — 그걸 재는 것이 폭입니다. "
            "**다만 이 지표는 취약성 지수에 넣지 않았습니다**: 검증 구간에서 고른 뒤 "
            "2021년 이후로 시험했더니 오히려 나빠졌습니다(-0.636 → -0.619). "
            "이미 있는 모멘텀·이격도와 축이 겹치는 것으로 보입니다. "
            "참고 지표로만 보세요."
        )

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

    # --- 한국 금리·크레딧 ---------------------------------------------
    st.divider()
    st.subheader("한국 금리 · 크레딧")
    ktb3 = macro_series(macro, "ecos.ktb3y")
    if ktb3.empty:
        st.info("한국 금리 데이터가 없습니다. `run_all --only ecos`")
    else:
        rates = {n: clip(macro_series(macro, k), start) for k, n in [
            ("ecos.ktb3y", "국고채 3년"), ("ecos.ktb10y", "국고채 10년"),
            ("ecos.corp_aa", "회사채 AA-"), ("ecos.corp_bbb", "회사채 BBB-"),
            ("ecos.cd91", "CD 91일"), ("ecos.base_rate", "기준금리")]}
        st.plotly_chart(
            charts.macro_lines({k: v for k, v in rates.items() if not v.empty},
                               "한국 금리 (연%)", mode=mode, ylabel="연%",
                               height=420),
            width="stretch")

        aa, bbb, k10 = (macro_series(macro, f"ecos.{x}")
                        for x in ("corp_aa", "corp_bbb", "ktb10y"))
        # ★ BBB- 는 수준이 한 자릿수 %p 로 AA-·장단기(0~2%p)와 자릿수가 다르다.
        #   한 축에 올리면 나머지가 바닥에 눌려 움직임이 안 보인다 — 따로 그린다.
        tight = {}
        if not aa.empty:
            tight["신용스프레드 (AA- − 국고3)"] = clip((aa - ktb3).dropna(), start)
        if not k10.empty:
            tight["장단기 (10년 − 3년)"] = clip((k10 - ktb3).dropna(), start)
        if tight:
            st.plotly_chart(
                charts.macro_lines(tight, "한국 스프레드 (%p)", mode=mode,
                                   ylabel="%p", zero_line=True, height=380),
                width="stretch")
            st.caption(
                "현재 " + " · ".join(f"{k.split('(')[0].strip()} {v.iloc[-1]:+.2f}%p"
                                    for k, v in tight.items() if not v.empty)
                + ". **장단기가 0 아래면 역전**이며 취약성 지수의 컴포넌트입니다. "
                  "신용스프레드(AA-) 확대는 우량 기업의 자금조달 스트레스를 뜻합니다."
            )
        if not bbb.empty:
            bs = clip((bbb - ktb3).dropna(), start)
            st.plotly_chart(
                charts.macro_lines({"저신용 스프레드 (BBB- − 국고3)": bs},
                                   "저신용 스프레드 (%p)", mode=mode,
                                   ylabel="%p", height=340),
                width="stretch")
            st.caption(
                f"현재 {bs.iloc[-1]:.2f}%p. BBB- 는 거래가 얇아 스프레드가 "
                "구조적으로 큽니다(6%p대). 위 차트와 자릿수가 달라 따로 그립니다 — "
                "같은 축에 올리면 AA-·장단기가 바닥에 눌려 안 보입니다."
            )

    # --- 실물 -----------------------------------------------------------
    exports, bsi = macro_series(macro, "ecos.exports"), macro_series(macro, "ecos.bsi")
    if not exports.empty or not bsi.empty:
        st.divider()
        st.subheader("실물 — 수출 · 기업체감")
        e1, e2 = st.columns(2)
        if not exports.empty:
            yoy = (exports.pct_change(12) * 100).dropna()
            e1.metric("수출 YoY", f"{yoy.iloc[-1]:+.1f}%",
                      f"{exports.iloc[-1]:,.1f}십억불")
            st.plotly_chart(
                charts.macro_lines({"수출 YoY": clip(yoy, start)},
                                   "수출 전년동월비 (%)", mode=mode, ylabel="%",
                                   zero_line=True, height=340),
                width="stretch")
        if not bsi.empty:
            e2.metric("전산업 BSI", f"{bsi.iloc[-1]:.0f}",
                      "100 미만 = 비관 우위", delta_color="off")
            st.plotly_chart(
                charts.macro_lines({"전산업 업황실적 BSI": clip(bsi, start)},
                                   "기업경기실사지수", mode=mode, ylabel="지수",
                                   height=340),
                width="stretch")
            st.caption("BSI 100 이 중립입니다. 수출 YoY 는 취약성 지수의 컴포넌트입니다.")

    st.divider()
    others = {"fred.DFF": "연방기금 실효금리", "fred.T10Y2Y": "미 10Y-2Y",
              "fred.T10Y3M": "미 10Y-3M", "fred.BAA10Y": "무디스 Baa 스프레드",
              "fred.AAA10Y": "무디스 Aaa 스프레드",
              "fred.BAMLH0A0HYM2": "하이일드 OAS (3년치만 공개)",
              "fred.M2SL": "미국 M2",
              "ecos.base_rate": "한국은행 기준금리", "ecos.m2": "한국 M2(평잔)",
              "ecos.usdkrw": "원/달러 환율",
              "ecos.kospi_marcap": "유가증권 시가총액(조원)",
              "ecos.kospi_value": "유가증권 거래대금(조원)",
              "ecos.kosdaq_value": "코스닥 거래대금(조원)",
              "ecos.foreign_net_kosdaq": "외국인 순매수(코스닥, 조원)",
              "ecos.margin_debt": "신용융자 잔고(조원)",
              "krx.2001_marcap": "코스닥 시가총액(조원)"}
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

        st.subheader("일간 수익률 상관")
        align = st.checkbox(
            "시차 정렬 (미국 자산을 하루 늦춤)", value=True, key="corr_align",
            help="한국장은 15:30 에 닫고 미국장은 그 뒤에 열립니다. 같은 날짜끼리 "
                 "비교하면 한국이 아직 모르는 정보와 짝지어져 관계가 과소평가됩니다.")
        corr = sea.correlation(prices, sorted(prices["ticker"].unique()),
                               start=start, align_sessions=align)
        if corr.empty:
            st.info("상관을 낼 공통 거래일이 부족합니다. 기간을 늘리세요.")
        else:
            st.plotly_chart(charts.correlation_heatmap(corr, mode=mode,
                                                       labels=names),
                            width="stretch")
            ks = corr.loc["KRX.1001"].drop("KRX.1001").sort_values(ascending=False)
            st.caption(
                "KOSPI 와 가장 높은 셋: "
                + " · ".join(f"{names.get(t, t)} {v:.2f}" for t, v in ks.head(3).items())
                + f" / 가장 낮은 하나: {names.get(ks.index[-1], ks.index[-1])} "
                  f"{ks.iloc[-1]:.2f}. "
                  "**시차 정렬을 끄면 미국 자산의 상관이 크게 낮아집니다** — "
                  "실측 SOX 0.40→0.15, S&P500 0.34→0.11. 같은 시간대인 닛케이·항셍은 "
                  "정렬 여부와 무관하게 그대로입니다."
            )

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
    if vres.get("error"):
        st.error(f"취약성 지수 계산 실패 — {vres['error']}")
        with st.expander("자세히"):
            st.code(vres.get("traceback", ""), language="text")
        st.caption("이 패널만 실패했고 다른 화면은 정상입니다. "
                   "배포 환경과 로컬의 패키지 버전이 다르면 이런 일이 생깁니다 — "
                   "requirements.txt 를 고정해 두었습니다.")
        vres = {}
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
    st.subheader("변동성 레짐")
    _k = close_of(prices, "KRX.1001")
    if _k.empty:
        st.info("KOSPI 데이터가 없습니다.")
    else:
        rv, rv_pr = sea.current_vol_percentile(_k)
        vstats = sea.vol_regime_stats(_k, horizon=60)
        v1, v2 = st.columns(2)
        v1.metric("20일 실현변동성 (연율)", f"{rv:.1%}",
                  f"{rv_pr:.0%} 분위" if rv_pr == rv_pr else None, delta_color="off")
        if not vstats.empty:
            hi = vstats["변동성 상한%"].iloc[-1]
            v2.metric("과거 최고 분위 상한", f"{hi:.1f}%",
                      "표본 범위 밖" if rv * 100 > hi else "표본 범위 안",
                      delta_color="off")
            st.dataframe(vstats, width="stretch")
            st.caption(
                "실현변동성 5분위별 **향후 60일 수익률**입니다. 과거에는 "
                "변동성이 높을수록 이후 수익률이 좋았습니다(공포 뒤 되돌림). "
                "다만 표본이 겹치므로 서술 통계로만 보세요."
            )
            if rv * 100 > hi:
                st.warning(
                    f"현재 변동성 {rv:.1%} 는 과거 최고 분위의 상한 {hi:.1f}% 를 "
                    "넘습니다. **표본 밖 구간이라 위 표를 그대로 적용하면 안 됩니다** — "
                    "'과거에 이랬으니 이번에도'가 가장 위험한 구간입니다."
                )

    st.divider()
    st.subheader("월별 계절성")
    if not _k.empty:
        mstats = sea.monthly_stats(_k)
        if mstats.empty:
            st.info("계절성을 낼 표본이 부족합니다.")
        else:
            st.plotly_chart(charts.monthly_seasonality(mstats, mode=mode),
                            width="stretch")
            st.dataframe(mstats, width="stretch")
            worst = mstats["평균%"].idxmin()
            best = mstats["평균%"].idxmax()
            st.caption(
                f"2005년 이후 KOSPI 기준. 가장 나쁜 달 **{worst}** "
                f"({mstats.loc[worst, '평균%']:+.2f}%, 상승확률 "
                f"{mstats.loc[worst, '상승확률%']:.0f}%), 가장 좋은 달 **{best}** "
                f"({mstats.loc[best, '평균%']:+.2f}%). "
                "**월당 표본이 21~22개뿐입니다** — 평균보다 상승확률과 표본 수를 "
                "함께 보시고, 매매 규칙으로 쓰지 마세요."
            )

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

with tab_data:
    st.subheader("데이터 카탈로그")
    st.caption(
        "수집 중인 모든 계열입니다. 계열이 60개를 넘으면 목록 없이는 관리가 안 됩니다 — "
        "반 년 뒤에 `ecos.corp_bbb` 가 뭔지 알 방법이 필요합니다. "
        "이름은 `config/series.yaml` 에서 읽으므로 코드와 갈라지지 않습니다."
    )

    cat = catalog_mod.build()
    if cat.empty:
        st.info("데이터가 없습니다. `python -m src.etl.run_all` 을 실행하세요.")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("계열 수", f"{len(cat):,}")
        c2.metric("총 관측", f"{cat['관측'].sum():,}")
        c3.metric("가장 오래된 관측", str(cat["시작"].min()))

        store_pick = st.multiselect("저장소", sorted(cat["저장소"].unique()),
                                    default=sorted(cat["저장소"].unique()),
                                    key="cat_store")
        query = st.text_input("검색 (계열명·이름·출처)", key="cat_q")
        view = cat[cat["저장소"].isin(store_pick)]
        if query:
            mask = view.apply(
                lambda r: query.lower() in " ".join(map(str, r)).lower(), axis=1)
            view = view[mask]
        st.dataframe(view, width="stretch", hide_index=True)

        stale = catalog_mod.staleness(cat, warn_days=7)
        if not stale.empty:
            with st.expander(f"7일 이상 갱신 안 된 계열 {len(stale)}건"):
                st.dataframe(stale[["계열", "이름", "최신", "지연(일)"]],
                             width="stretch", hide_index=True)
                st.caption(
                    "월간 계열(M2·수출·예탁금·신용융자·BSI)은 원래 느립니다 — "
                    "한국 M2 는 약 2개월 지연 공표됩니다. "
                    "**일간 계열이 여기 보이면 그건 문제**입니다."
                )

    st.divider()
    st.subheader("내려받기")
    st.caption("엑셀 등에서 따로 보실 때. 대시보드가 읽는 것과 같은 파일입니다.")
    dl = st.columns(3)
    for col, name in zip(dl * 2, ["macro", "prices", "flows",
                                  "vintages", "snapshots"]):
        df = load(name)
        if df.empty:
            continue
        col.download_button(
            f"{name}.csv ({len(df):,}행)",
            df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"{name}.csv", mime="text/csv",
            key=f"dl_{name}", width="stretch")
    st.caption("한글이 깨지지 않도록 UTF-8 BOM 으로 저장됩니다 (엑셀 호환).")


with tab_lev:
    st.subheader("신용(레버리지) 위험")
    st.caption(
        "**예측이 아니라 파산 확률 문제입니다.** 60일 뒤 지수가 제자리로 돌아와도 "
        "그 사이 한 번만 담보비율을 깨면 이미 청산된 뒤입니다. "
        "'장기적으로는 오른다'가 레버리지에서 통하지 않는 이유입니다."
    )

    lc1, lc2 = st.columns(2)
    my_lev = lc1.slider("내 레버리지 (총자산 ÷ 자기자본)", 1.0, 3.0, 2.0, 0.1,
                        key="my_lev",
                        help="2.0 = 자기자본만큼 빌림 (융자 비중 50%)")
    maint = lc2.slider("담보유지비율 (%)", 120, 170, 140, 5, key="maint",
                       help="증권사·종목별로 다릅니다. 보통 140%") / 100

    kospi_l = close_of(prices, "KRX.1001")
    if kospi_l.empty:
        st.info("KOSPI 데이터가 없습니다.")
    else:
        x = lev.margin_call_drawdown(my_lev, maint)
        dd_now = float(vu.drawdown(kospi_l).iloc[-1])
        loc_dd = float(lev.local_drawdown(kospi_l).iloc[-1])

        m1, m2, m3 = st.columns(3)
        m1.metric("반대매매까지", f"-{x * 100:.1f}%",
                  "여기서 더 빠지면 강제청산", delta_color="off")
        m2.metric("전고점 대비", f"{dd_now:.1%}")
        m3.metric("1년 고점 대비", f"{loc_dd:.1%}",
                  "레버리지엔 이쪽이 더 맞습니다", delta_color="off")

        st.markdown("**레버리지별 임계 — 담보유지 {:.0f}% 기준**".format(maint * 100))
        st.dataframe(lev.leverage_table(maint), width="stretch", hide_index=True)

        st.markdown("**과거 조정에서 살아남았나**")
        ep_l = vu.episodes(kospi_l)
        surv = []
        for L in (1.5, 1.8, 2.0, 2.5, 3.0):
            xx = lev.margin_call_drawdown(L, maint)
            surv.append({"레버리지": f"{L:.1f}x",
                         "임계 하락률%": round(xx * 100, 1),
                         f"{len(ep_l)}회 중 청산": int((ep_l["depth"] <= -xx).sum())})
        st.dataframe(pd.DataFrame(surv), width="stretch", hide_index=True)
        st.dataframe(lev.survival_by_leverage(kospi_l, maintenance=maint),
                     width="stretch", hide_index=True)

        vres_l = vulnerability_result()
        if vres_l and not vres_l.get("error"):
            cond = lev.conditional_risk(vres_l["index"], kospi_l,
                                        leverage=my_lev, maintenance=maint,
                                        condition=vu.near_high(kospi_l))
            if not cond.empty:
                st.markdown(f"**취약성 분위별 반대매매 확률 ({my_lev:.1f}x)**")
                st.dataframe(cond, width="stretch")
                st.caption(
                    "취약성 지수는 조정 **시점**을 절반쯤 놓칩니다. 하지만 "
                    "레버리지 판단에는 시점이 아니라 **확률**이면 충분합니다 — "
                    "분위 간 차이가 몇 배로 벌어지면 결정을 바꾸기에 충분합니다."
                )

        md_sig = lev.margin_debt_signal(macro_series(macro, "ecos.margin_debt"),
                                        macro_series(macro, "ecos.kospi_marcap"))
        vres_ok = vres_l and not vres_l.get("error")
        chk = lev.deleverage_checklist(
            drawdown=dd_now,
            vulnerability=(float(vres_l["index"].dropna().iloc[-1])
                           if vres_ok and not vres_l["index"].dropna().empty else None),
            applicable=bool(vu.near_high(kospi_l).iloc[-1]),
            margin_pr=(float(md_sig["백분위"].iloc[-1]) if not md_sig.empty else None),
            leverage=my_lev, maintenance=maint)
        st.markdown("**판단 재료**")
        icon = {"ok": "🟢", "warn": "🟡", "bad": "🔴", "info": "⚪"}
        for lv_, label, val in chk:
            st.markdown(f"{icon.get(lv_, '•')} **{label}** — {val}")
        st.caption(
            "**자동 매매 신호가 아닙니다.** '지금 꺼라'를 지표가 말하게 하면 "
            "그 지표가 틀렸을 때 책임질 방법이 없습니다. 재료를 보고 판단은 "
            "직접 하세요."
        )

    st.divider()
    st.subheader("예탁금 vs 신용융자")
    dm = fv.deposit_vs_margin(macro_series(macro, "ecos.investor_deposit"),
                              macro_series(macro, "ecos.margin_debt"))
    if dm.empty:
        st.info("예탁금·신용융자 데이터가 없습니다.")
    else:
        d1, d2, d3 = st.columns(3)
        d1.metric("예탁금", f"{dm['예탁금'].iloc[-1]:,.1f}조")
        d2.metric("신용융자", f"{dm['신용융자'].iloc[-1]:,.1f}조")
        d3.metric("신용/예탁금", f"{dm['신용/예탁금%'].iloc[-1]:.1f}%",
                  f"역대 최고 {dm['신용/예탁금%'].max():.1f}%", delta_color="off")
        st.plotly_chart(
            charts.macro_lines({"예탁금": clip(dm["예탁금"], start),
                                "신용융자": clip(dm["신용융자"], start)},
                               "예탁금 · 신용융자 (조원)", mode=mode,
                               ylabel="조원", height=380),
            width="stretch")
        st.plotly_chart(
            charts.macro_lines({"신용/예탁금": clip(dm["신용/예탁금%"], start)},
                               "신용융자 ÷ 예탁금 (%)", mode=mode,
                               ylabel="%", height=340),
            width="stretch")
        st.caption(
            "예탁금은 **대기 자금**, 신용융자는 **빌려서 이미 산 돈**입니다. "
            "비율이 오르면 시장이 현금보다 빚으로 굴러간다는 뜻이고, 조정 때 "
            "반대매매로 증폭될 여지가 커집니다. "
            f"역대 최고는 {dm['신용/예탁금%'].idxmax().date()} 의 "
            f"{dm['신용/예탁금%'].max():.1f}% 였습니다."
        )

    st.divider()
    st.subheader("누적 순매수 — 누가 사고 누가 팔았나")
    if flows.empty:
        st.info("수급 데이터가 없습니다.")
    else:
        f1, f2 = st.columns([2, 3])
        mk_c = f1.radio("시장", sorted(flows["market"].unique()),
                        horizontal=True, key="cum_market")
        since = f2.selectbox(
            "누적 시작", ["2005년부터", "10년", "5년", "3년", "1년"],
            key="cum_since",
            help="누적선은 시작점에 따라 모양이 완전히 달라집니다")
        days = {"10년": 365 * 10, "5년": 365 * 5, "3년": 365 * 3, "1년": 365}
        st0 = (None if since == "2005년부터"
               else pd.Timestamp.today().normalize() - pd.Timedelta(days=days[since]))
        cum = fv.cumulative(flows, mk_c, start=st0)
        if cum.empty:
            st.info("누적할 데이터가 없습니다.")
        else:
            order = [c for c in ["개인", "외국인합계", "기관합계", "기타법인"]
                     if c in cum.columns]
            st.plotly_chart(
                charts.macro_lines({c: cum[c] for c in order},
                                   f"{mk_c} 누적 순매수 ({since}, 조원)",
                                   mode=mode, ylabel="조원", zero_line=True,
                                   height=440),
                width="stretch")
            last = cum.iloc[-1]
            st.caption(
                "현재 누적 " + " · ".join(f"{c} {last[c]:+,.0f}조" for c in order)
                + ". 일간 순매수는 잡음이지만 **누적하면 구조가 보입니다** — "
                  "누가 몇 년째 팔고 누가 받아냈는지는 이 선에서만 드러납니다."
            )
