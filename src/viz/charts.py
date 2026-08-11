"""Plotly figure 팩토리 (CLAUDE.md §5.8).

규칙
- 모든 함수는 figure 를 만들어 반환만 한다. st.* 호출은 kpi_row 만 예외.
- 계열이 2개 이상이면 범례를 항상 켠다 (색만으로 식별하게 두지 않는다).
- 눈금·격자는 후퇴색, 데이터 마크는 2px.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from src.indicators import technical as ta
from src.viz import theme


def _diverging(mode: str) -> tuple[str, str]:
    """양/음 극성용 두 색 (파랑↔빨강). 중립 중간값은 격자색이 대신한다."""
    p = theme.palette(mode)
    return p["series"][0], p["series"][7]


def price_macd_rsi(df: pd.DataFrame, title: str, *, ma=(20, 60, 120),
                   rsi_n: int = 14, fast: int = 12, slow: int = 26,
                   signal: int = 9, mode: str = "light",
                   color_key: str | None = None) -> go.Figure:
    """3단 서브플롯 (가격+MA / MACD+히스토그램 / RSI).

    df: DatetimeIndex + 'close' 컬럼. 나머지 컬럼은 무시한다.
    """
    p = theme.palette(mode)
    close = df["close"].astype("float64")
    base = theme.series_color(color_key or title, mode)

    # subplot_titles 대신 y축 제목을 쓴다 — 부유 주석은 플롯 영역을 침범한다
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                        row_heights=[0.5, 0.25, 0.25], vertical_spacing=0.05)

    # --- 1단: 가격 + 이동평균 ---
    fig.add_trace(go.Scatter(
        x=close.index, y=close, name="종가", mode="lines",
        line=dict(color=base, width=2),
        hovertemplate="%{y:,.2f}<extra>종가</extra>"), row=1, col=1)

    # 종가가 쓴 슬롯만 건너뛰고 나머지는 팔레트 순서를 그대로 지킨다.
    # 슬롯 순서 자체가 색각이상 안전장치라 임의 재배열은 검증 범위를 벗어난다.
    ma_colors = [c for c in p["series"] if c != base]
    for i, n in enumerate(ma):
        fig.add_trace(go.Scatter(
            x=close.index, y=close.rolling(n).mean(), name=f"MA{n}",
            mode="lines",
            line=dict(color=ma_colors[i % len(ma_colors)],
                      width=1.5, dash="dot"),
            hovertemplate="%{y:,.2f}<extra>MA" + str(n) + "</extra>"),
            row=1, col=1)

    # --- 2단: MACD ---
    m = ta.macd(close, fast, slow, signal)
    pos, neg = _diverging(mode)
    fig.add_trace(go.Bar(
        x=m.index, y=m["hist"], name="히스토그램",
        marker=dict(color=[pos if v >= 0 else neg for v in m["hist"]],
                    line=dict(width=0)),
        hovertemplate="%{y:,.3f}<extra>hist</extra>"), row=2, col=1)
    # 데이터 마크는 계열색을 입는다 — 텍스트 토큰(회색)을 마크에 쓰지 않는다
    fig.add_trace(go.Scatter(
        x=m.index, y=m["macd"], name="MACD", mode="lines",
        line=dict(color=p["series"][6], width=2),
        hovertemplate="%{y:,.3f}<extra>MACD</extra>"), row=2, col=1)
    fig.add_trace(go.Scatter(
        # 슬롯 4 — 1단의 이동평균(0~3)과 범례에서 색이 겹치지 않게 띄운다
        x=m.index, y=m["signal"], name="시그널", mode="lines",
        line=dict(color=p["series"][4], width=1.5),
        hovertemplate="%{y:,.3f}<extra>시그널</extra>"), row=2, col=1)

    # --- 3단: RSI ---
    r = ta.rsi(close, rsi_n)
    fig.add_trace(go.Scatter(
        x=r.index, y=r, name=f"RSI({rsi_n})", mode="lines",
        line=dict(color=p["series"][5], width=2),
        hovertemplate="%{y:.1f}<extra>RSI</extra>"), row=3, col=1)
    # 라벨을 플롯 안쪽 왼쪽에 둔다 — "right" 는 오른쪽 여백 밖으로 나가 잘린다
    for level, label in ((70, "과매수 70"), (30, "과매도 30")):
        fig.add_hline(y=level, line=dict(color=p["muted"], width=1, dash="dash"),
                      annotation_text=label, annotation_position="top left",
                      annotation_font=dict(color=p["muted"], size=10),
                      row=3, col=1)

    fig.update_yaxes(title_text="지수", row=1, col=1)
    fig.update_yaxes(title_text="MACD", row=2, col=1)
    fig.update_yaxes(title_text=f"RSI({rsi_n})", range=[0, 100], row=3, col=1)

    fig.update_layout(title=title, barmode="relative", bargap=0.1)
    # rangeslider 비활성 — 좁은 화면에서 방해된다
    fig.update_xaxes(rangeslider_visible=False)
    return theme.apply(fig, mode, height=800)


def liquidity_overlay(liq: pd.Series, price: pd.Series, title: str, *,
                      mode: str = "light",
                      scale: str = "stacked") -> go.Figure:
    """유동성과 지수를 함께 본다. 세 가지 표현을 지원한다.

    scale="stacked" (기본): x축을 공유하는 상/하 2단. 각자 자기 눈금을 쓰되
                            세로로 정렬돼 있어 동행/역행을 그대로 읽을 수 있다.
    scale="indexed"       : 공통 시작점=100 지수화, 단일 축.
    scale="dual_axis"     : CLAUDE.md §5.8 원문대로 좌/우 이중축.

    기본을 stacked 로 둔 이유는 두 가지다.
    - 이중축은 두 계열의 눈금을 임의로 정렬하는 행위라, 같은 데이터로 '동행'과
      '역행'을 모두 만들어낼 수 있다.
    - 지수화는 축은 정직하지만 변동폭이 크게 다를 때(지수 +240% vs 유동성 ±10%)
      작은 쪽이 평평한 직선으로 눌려 아무것도 읽히지 않는다.
    """
    p = theme.palette(mode)
    c_liq, c_px = p["series"][0], p["series"][1]

    l, x = liq.dropna(), price.dropna()
    if l.empty or x.empty:
        return theme.apply(go.Figure(layout_title_text=f"{title} — 데이터 없음"),
                           mode, height=420)

    # 공통 '구간'으로 자른다. index 교집합으로 조인하면 주간 계열에 맞춰
    # 일간 지수가 주 1회로 솎여 나간다(관측치 손실).
    lo = max(l.index.min(), x.index.min())
    hi = min(l.index.max(), x.index.max())
    if lo > hi:
        return theme.apply(go.Figure(layout_title_text=f"{title} — 겹치는 구간 없음"),
                           mode, height=420)
    l, x = l[(l.index >= lo) & (l.index <= hi)], x[(x.index >= lo) & (x.index <= hi)]

    if scale == "stacked":
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                            row_heights=[0.5, 0.5], vertical_spacing=0.06)
        fig.add_trace(go.Scatter(x=l.index, y=l, name="순유동성", mode="lines",
                                 line=dict(color=c_liq, width=2),
                                 hovertemplate="%{y:,.0f}<extra>유동성</extra>"),
                      row=1, col=1)
        fig.add_trace(go.Scatter(x=x.index, y=x, name="지수", mode="lines",
                                 line=dict(color=c_px, width=2),
                                 hovertemplate="%{y:,.2f}<extra>지수</extra>"),
                      row=2, col=1)
        fig.update_yaxes(title_text="순유동성 (십억$)", row=1, col=1)
        fig.update_yaxes(title_text="지수", row=2, col=1)
        fig.update_layout(title=title)
        return theme.apply(fig, mode, height=560)

    if scale == "dual_axis":
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(go.Scatter(x=l.index, y=l, name="순유동성 (십억$)",
                                 mode="lines", line=dict(color=c_liq, width=2),
                                 hovertemplate="%{y:,.0f}<extra>유동성</extra>"),
                      secondary_y=False)
        fig.add_trace(go.Scatter(x=x.index, y=x, name="지수", mode="lines",
                                 line=dict(color=c_px, width=2),
                                 hovertemplate="%{y:,.2f}<extra>지수</extra>"),
                      secondary_y=True)
        fig.update_yaxes(title_text="순유동성 (십억$)", secondary_y=False,
                         gridcolor=p["grid"])
        fig.update_yaxes(title_text="지수", secondary_y=True, showgrid=False)
    else:
        li = l / l.iloc[0] * 100.0
        xi = x / x.iloc[0] * 100.0
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=li.index, y=li, name="순유동성", mode="lines",
                                 line=dict(color=c_liq, width=2),
                                 hovertemplate="%{y:,.1f}<extra>유동성</extra>"))
        fig.add_trace(go.Scatter(x=xi.index, y=xi, name="지수", mode="lines",
                                 line=dict(color=c_px, width=2),
                                 hovertemplate="%{y:,.1f}<extra>지수</extra>"))
        fig.add_hline(y=100, line=dict(color=p["axis"], width=1))
        fig.update_yaxes(title_text=f"{li.index[0].date()} = 100")

    fig.update_layout(title=title)
    return theme.apply(fig, mode, height=440)


def disparity_bands(close: pd.Series, windows=(20, 60, 120), *,
                    mode: str = "light", title: str = "이격도") -> go.Figure:
    """복수 기간 이격도. 100 이 기준선(=이동평균과 같음)."""
    p = theme.palette(mode)
    fig = go.Figure()
    for i, n in enumerate(windows):
        d = ta.disparity(close.astype("float64"), n)
        fig.add_trace(go.Scatter(
            x=d.index, y=d, name=f"이격도 {n}일", mode="lines",
            line=dict(color=p["series"][i % len(p["series"])], width=2),
            hovertemplate="%{y:.1f}<extra>" + f"{n}일" + "</extra>"))
    # 안쪽 배치 — "right" 는 오른쪽 여백 밖으로 나가 잘린다
    fig.add_hline(y=100, line=dict(color=p["axis"], width=1),
                  annotation_text="100 = 이동평균", annotation_position="top left",
                  annotation_font=dict(color=p["muted"], size=10))
    fig.update_layout(title=title)
    return theme.apply(fig, mode, height=420)


def investor_flow_bar(flows: pd.DataFrame, market: str, *,
                      mode: str = "light", investors=None,
                      freq: str | None = None) -> go.Figure:
    """투자자별 순매수 대금. 양수=순매수, 음수=순매도.

    investors 를 주지 않으면 데이터에 실제로 있는 구분을 쓴다.
    이름을 하드코딩하면 소스가 '외국인'→'외국인합계' 처럼 바뀔 때
    계열이 조용히 사라진다(에러도 안 난다).

    freq: 'W'/'ME' 등. 일간 막대는 수천 개가 겹쳐 읽히지 않으므로
    긴 구간에서는 합산해서 본다.
    """
    p = theme.palette(mode)
    sub = flows[flows["market"] == market]
    if investors is None:
        order = ["개인", "외국인합계", "기관합계", "기타법인"]
        present = set(sub["investor"])
        investors = [i for i in order if i in present] + \
                    sorted(present - set(order))

    fig = go.Figure()
    for inv in investors:
        s = sub[sub["investor"] == inv]
        if s.empty:
            continue
        if freq:
            s = (s.set_index("date")["net_value"].resample(freq).sum()
                 .rename("net_value").reset_index())
        fig.add_trace(go.Bar(
            x=s["date"], y=s["net_value"] / 1e8, name=inv,
            marker=dict(color=theme.series_color(inv, mode, theme.INVESTOR_SLOT),
                        line=dict(width=0)),
            hovertemplate="%{y:,.0f}억<extra>" + inv + "</extra>"))
    fig.add_hline(y=0, line=dict(color=p["axis"], width=1))
    label = {"W": " (주간 합계)", "ME": " (월간 합계)"}.get(freq or "", "")
    fig.update_layout(title=f"{market} 투자자별 순매수{label} — 억원",
                      barmode="relative", bargap=0.15)
    fig.update_yaxes(title_text="억원")
    return theme.apply(fig, mode, height=440)


def kpi_row(metrics: dict) -> None:
    """st.columns + st.metric. metrics = {라벨: (값, 델타)} 또는 {라벨: 값}."""
    import streamlit as st

    if not metrics:
        return
    cols = st.columns(len(metrics))
    for col, (label, v) in zip(cols, metrics.items()):
        value, delta = v if isinstance(v, tuple) else (v, None)
        col.metric(label, value, delta)


def add_episode_shading(fig, ep: pd.DataFrame, *, mode: str = "light",
                        row: int | None = None) -> None:
    """조정 국면을 세로 음영으로 표시한다 (고점 → 저점).

    저점까지만 칠하는 이유: 회복 구간까지 칠하면 화면 절반이 음영이 된다
    (KOSPI 는 전체 일수의 39%가 -15% 이하 낙폭 상태였다).
    위험이 '진행된' 구간만 보여주는 편이 눈에 들어온다.
    """
    p = theme.palette(mode)
    fill = "rgba(227,73,72,0.10)" if mode == "light" else "rgba(230,103,103,0.13)"
    for _, r in ep.iterrows():
        # pandas Timestamp 를 그대로 넘기면 이미지 내보내기에서 직렬화가 깨진다
        kw = dict(x0=pd.Timestamp(r["peak"]).isoformat(),
                  x1=pd.Timestamp(r["trough"]).isoformat(),
                  fillcolor=fill, line_width=0, layer="below")
        if row is not None:
            fig.add_vrect(row=row, col=1, **kw)
        else:
            fig.add_vrect(**kw)


def vulnerability_panel(index: pd.Series, close: pd.Series, ep: pd.DataFrame,
                        *, mode: str = "light", threshold: float = 0.8,
                        applicable: pd.Series | None = None) -> go.Figure:
    """상단 지수(음영 포함) + 하단 취약성. x축 공유."""
    p = theme.palette(mode)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.55, 0.45], vertical_spacing=0.06)

    fig.add_trace(go.Scatter(x=close.index, y=close, name="지수", mode="lines",
                             line=dict(color=p["series"][0], width=2),
                             hovertemplate="%{y:,.0f}<extra>지수</extra>"),
                  row=1, col=1)
    # 20년 구간을 선형축으로 그리면 최근 급등에 눌려 과거가 평평해진다.
    # 로그축이라야 2008년 -54% 와 2026년 -39% 가 비슷한 기울기로 보인다.
    fig.update_yaxes(type="log", row=1, col=1)

    fig.add_trace(go.Scatter(x=index.index, y=index, name="취약성", mode="lines",
                             line=dict(color=p["series"][7], width=2),
                             hovertemplate="%{y:.2f}<extra>취약성</extra>"),
                  row=2, col=1)

    if applicable is not None:
        # 조건 밖(이미 조정 중) 구간은 회색으로 덮어 '해석 불가'를 드러낸다
        masked = index.where(~applicable.reindex(index.index).fillna(False))
        fig.add_trace(go.Scatter(x=masked.index, y=masked, name="적용 밖(조정 진행 중)",
                                 mode="lines", line=dict(color=p["muted"], width=2),
                                 hovertemplate="%{y:.2f}<extra>적용 밖</extra>"),
                      row=2, col=1)

    fig.add_hline(y=threshold, line=dict(color=p["muted"], width=1, dash="dash"),
                  annotation_text=f"{threshold:.0%} 경계",
                  annotation_position="top left",
                  annotation_font=dict(color=p["muted"], size=10), row=2, col=1)

    if not ep.empty:
        add_episode_shading(fig, ep, mode=mode, row=1)
        add_episode_shading(fig, ep, mode=mode, row=2)

    fig.update_yaxes(title_text="지수", row=1, col=1)
    fig.update_yaxes(title_text="취약성", range=[0, 1], row=2, col=1)
    fig.update_layout(title="취약성 지수 — 음영은 -15% 이상 조정 구간(고점→저점)")
    return theme.apply(fig, mode, height=620)
