"""레버리지 ETF 신호 페이지 — '현세의 주식' 블로그 바닥 매수 규칙 (SOXL·TQQQ).

화면 순서: 한눈 요약(종목별 카드) → 선택 종목 상세(지표·체크리스트·차트) → 접힌 근거(성과·이력·규칙).
규칙 정의는 src/research/blog_signals.py, 대상 종목은 series.yaml 의 signals: true.
★ 외부 API 호출 없음 (설계원칙 1).
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from config import settings
from src.research import blog_signals as bsig
from src.viz import charts
from src.viz.ui import (data_stamp, fmt_delta, freshness, load, load_meta, mode,
                        ohlcv_of, ticker_names)

#: 단계 → (짧은 이름, 배지 색, 한 줄 설명)
STAGE = {
    None: ("관망", "gray", "매수 조건이 아직 없습니다"),
    "pit": ("1단계", "orange", "과매도 웅덩이 · 1차 매수 구간, 확인 신호 대기"),
    "obv_gold": ("2단계", "green", "웅덩이 + OBV gold · 블로그 2차(본격) 매수 조건"),
    "di_gold": ("3단계", "green", "입 닫으며 DI gold · 댓글의 '더 확실한' 매수 조건"),
    "sell_w": ("매도", "red", "주봉 상단 웅덩이 · 블로그 매도 조건"),
}
STEPS = [("pit", "1 웅덩이"), ("obv_gold", "2 OBV gold"), ("di_gold", "3 DI gold")]


@st.cache_data(ttl=3600)
def _bundle(ticker: str, stamp: float) -> dict:
    d = ohlcv_of(load("prices"), ticker)
    if len(d) < 150:
        return {}
    return {"d": d, "f": bsig.features(d), "sig": bsig.signals(d),
            "check": bsig.checklist(d), "now": bsig.stage_now(d),
            "study": bsig.event_study(d), "hist": bsig.history(d)}


def bundle(ticker: str) -> dict:
    return _bundle(ticker, data_stamp())


def steps_md(on: dict) -> str:
    """단계 진행 배지. 최근 3거래일 안에 켜진 단계만 색을 입힌다."""
    parts = [f":{'green' if on.get(k) else 'gray'}-badge[{lab}]" for k, lab in STEPS]
    if on.get("sell_w"):
        parts.append(":red-badge[매도]")
    return " ".join(parts)


# --------------------------------------------------------------- 헤더
names = ticker_names()
prices = load("prices")
tickers = [f"YF.{i['ticker']}" for i in settings.series_for("yfinance") if i.get("signals")]
tickers = [t for t in tickers if (prices["ticker"] == t).any()]

st.title("레버리지 ETF 신호")
st.caption("'현세의 주식' 블로그의 보조지표 바닥 매수 규칙을 수치로 옮긴 화면입니다. "
           "투자 권유가 아니라 규칙의 현재 상태와 과거 성과를 보여줍니다.")
freshness(load_meta(), sources=["us_equity"])

if not tickers:
    st.info("SOXL/TQQQ 데이터가 아직 없습니다. 다음 자동 수집(us_equity) 후 나타납니다.")
    st.stop()

bundles = {t: bundle(t) for t in tickers}
tickers = [t for t in tickers if bundles[t]]

# --------------------------------------------------------------- 한눈 요약
cols = st.columns(len(tickers))
for col, tk in zip(cols, tickers):
    b = bundles[tk]
    now, c = b["now"], b["d"]["close"]
    label, color, desc = STAGE[now["level"]]
    with col.container(border=True):
        h1, h2 = st.columns([3, 2], vertical_alignment="center")
        h1.markdown(f"**{names.get(tk, tk)}**")
        h2.markdown(f":{color}-badge[{label}]", text_alignment="right")
        st.metric(f"종가 · {now['asof']:%m/%d}", f"{c.iloc[-1]:,.2f}", fmt_delta(c))
        st.markdown(steps_md(now["on"]))
        st.caption(desc)

# --------------------------------------------------------------- 상세
st.write("")
tk = st.segmented_control("상세 보기", tickers, default=tickers[0], key="sig_tk", required=True,
                          format_func=lambda t: names.get(t, t).split(" ")[0]) or tickers[0]
b = bundles[tk]
f, sig = b["f"], b["sig"]
last = f.iloc[-1]
c = b["d"]["close"]

m1, m2, m3, m4 = st.columns(4)
m1.metric("RSI(14)", f"{last.rsi:.1f}", help="30 아래가 과매도")
m2.metric("Slow %K", f"{last.sk:.1f}", help="20 아래가 과매도")
m3.metric("MDI − PDI", f"{last.spread:.1f}",
          help="하락 추세의 '입 벌림'. 클수록 매도 우위가 극단적")
m4.metric("52주 고점 대비", f"{c.iloc[-1] / c.tail(252).max() - 1:.1%}")

with st.container(border=True):
    st.markdown("**규칙 체크리스트**  \n"
                "<span style='opacity:.6;font-size:.85rem'>과매도는 최근 3일 안 발생, "
                "OBV gold 는 최근 3일 안 교차 기준</span>", unsafe_allow_html=True)
    items = b["check"]
    for row in range(0, len(items), 4):                 # 행 단위로 그려야 칸이 맞는다
        for col, r in zip(st.columns(4), items[row:row + 4]):
            dot = ":green[●]" if r["ok"] else ":gray[○]"
            col.markdown(f"{dot} **{r['name']}**  \n"
                         f"<span style='opacity:.65;font-size:.82rem'>{r['detail']}</span>",
                         unsafe_allow_html=True)

span = st.segmented_control("차트 기간", ["6개월", "1년", "3년", "전체"], default="1년",
                            key="sig_span", required=True) or "1년"
if span == "전체":
    view = f.loc["2011":]
else:
    days = {"6개월": 182, "1년": 365, "3년": 365 * 3}[span]
    view = f.loc[f.index >= f.index[-1] - pd.Timedelta(days=days)]
st.plotly_chart(charts.blog_signal_panel(view, sig.loc[view.index],
                                         f"{names.get(tk, tk)} 신호 패널", mode=mode()),
                width="stretch")

# --------------------------------------------------------------- 근거 (접힘)
with st.expander("이 규칙은 과거에 얼마나 맞았나 (2011~)"):
    es = b["study"].set_index("단계")
    show = pd.DataFrame({
        "N": es["N"],
        "20일 후": es["20일 중앙값"] * 100,
        "60일 후": es["60일 중앙값"] * 100,
        "120일 후": es["120일 중앙값"] * 100,
        "60일 상승확률": es["60일 상승확률"] * 100,
        "20일 최대역행": es["20일 최대역행(중앙)"] * 100,
        "우연일 확률(60일)": es["p60"],
    })
    num = st.column_config.NumberColumn
    st.dataframe(show, width="stretch", column_config={
        **{k: num(k, format="%.1f%%") for k in
           ["20일 후", "60일 후", "120일 후", "60일 상승확률", "20일 최대역행"]},
        "우연일 확률(60일)": num("우연일 확률(60일)", format="%.2f",
                             help="같은 수의 랜덤 진입일 중앙값이 신호 중앙값 이상일 확률"),
    })
    base = es.iloc[0]
    st.caption(
        f"신호 다음날 시가 진입, 수익률은 중앙값. 기준선(아무 날이나 샀을 때): "
        f"20일 {base['20일 기준선']:.1%} · 60일 {base['60일 기준선']:.1%} · "
        f"120일 {base['120일 기준선']:.1%}. 20거래일 안에 반복된 신호는 1회로 셉니다.")
    st.markdown(
        "- 표본(N)이 10~40회라 통계적 확신은 약합니다.\n"
        "- 3중 웅덩이는 사실상 RSI<30 하나와 같은 정보입니다(세 지표 모두 같은 가격에서 계산). "
        "정보를 더하는 것은 OBV·DI 확인 신호입니다.\n"
        "- 원 규칙에는 손절 조건이 없습니다.")

with st.expander("신호 발생 이력"):
    h = b["hist"].copy()
    if h.empty:
        st.write("발생 이력 없음")
    else:
        for k in ["r20", "r60", "r120", "mae20"]:
            h[k] = h[k] * 100
        h.index = h.index.strftime("%Y-%m-%d")
        h = h.rename(columns={"r20": "20일 후", "r60": "60일 후", "r120": "120일 후",
                              "mae20": "20일 최대역행"})
        st.dataframe(h, width="stretch", column_config={
            k: st.column_config.NumberColumn(k, format="%.1f%%")
            for k in ["20일 후", "60일 후", "120일 후", "20일 최대역행"]})

with st.expander("규칙 원문과 정량화 기준 · 알림"):
    st.markdown(
        "| 단계 | 원문 | 정량화 | 시간축 |\n|---|---|---|---|\n"
        "| 1단계 웅덩이 | Stoch·RSI(·CCI) 과매도 웅덩이 | 최근 3일 안 Slow %K(5,3,3)<20, "
        "RSI(14)<30, CCI(20)<−100 | 일봉 |\n"
        "| 입 최대 | ADX 데드 후 PDI·MDI 입 최대 | MDI−PDI 가 60일 최대 (최근 10일) | 일봉 |\n"
        "| 2단계 OBV gold | OBV gold | 웅덩이 중 OBV 가 9일 SMA 상향 돌파 | 일봉 |\n"
        "| 3단계 DI gold | 입을 닫으며 gold | 입 최대 후 30거래일 안 PDI>MDI 교차 | 일봉 |\n"
        "| 매도 | Stoch 상단 웅덩이 + OBV 이격 | 주봉 Slow(10,5,5) 과매수 후 K<D "
        "+ OBV−신호선 축소 | 주봉 |")
    st.caption(
        "출처: 블로그 글(2025-08-19)과 블로그 주인 댓글(2026-11-04). 2025-04-04 웅덩이 → 04-07 "
        "OBV gold, 주봉 매도 2025-08-04 로 블로그 매매일을 재현합니다. "
        "텔레그램 알림은 각 단계가 새로 켜질 때 한 번 보내며, 미국 정규장 중 30분마다 갱신합니다"
        "(장중 알림은 '잠정').")
