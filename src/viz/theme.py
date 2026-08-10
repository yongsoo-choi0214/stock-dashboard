"""색상/레이아웃 상수 (CLAUDE.md §5.8 viz/theme.py).

팔레트는 CVD(색각이상) 검증을 통과한 순서를 그대로 쓴다. 슬롯 순서 자체가
안전장치이므로 임의로 섞지 말 것. 계열 색은 '순위'가 아니라 '개체'를 따라간다.
"""
from __future__ import annotations

# 카테고리 팔레트 — 고정 순서. 9번째 계열은 새 색을 만들지 말고 '기타'로 접는다.
SERIES_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
                "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
SERIES_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500",
               "#d55181", "#008300", "#9085e9", "#e66767"]

# 상태색 — 계열색으로 재사용 금지. 항상 라벨과 함께 쓴다.
STATUS = {"good": "#0ca30c", "warning": "#fab219",
          "serious": "#ec835a", "critical": "#d03b3b"}

LIGHT = {
    "surface": "#fcfcfb",
    "plane": "#f9f9f7",
    "text": "#0b0b0b",
    "text_secondary": "#52514e",
    "muted": "#898781",
    "grid": "#e1e0d9",
    "axis": "#c3c2b7",
    "up": "#006300",
    "series": SERIES_LIGHT,
}

DARK = {
    "surface": "#1a1a19",
    "plane": "#0d0d0d",
    "text": "#ffffff",
    "text_secondary": "#c3c2b7",
    "muted": "#898781",
    "grid": "#2c2c2a",
    "axis": "#383835",
    "up": "#0ca30c",
    "series": SERIES_DARK,
}

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'

# 개체 → 색 슬롯 고정. 필터로 계열이 빠져도 남은 계열의 색이 바뀌지 않는다.
TICKER_SLOT = {
    "KRX.1001": 0, "KRX.2001": 1, "KRX.1028": 2,
    "YF.^GSPC": 0, "YF.^IXIC": 1, "YF.^VIX": 7, "YF.DX-Y.NYB": 3,
}

INVESTOR_SLOT = {"개인": 0, "외국인": 1, "기관합계": 2, "기타법인": 4, "기타외국인": 6}


def palette(mode: str = "light") -> dict:
    return DARK if mode == "dark" else LIGHT


def series_color(key: str, mode: str = "light",
                 slots: dict[str, int] | None = None) -> str:
    """개체 키 → 고정 색. 매핑에 없으면 해시로 안정적 슬롯을 배정한다."""
    p = palette(mode)
    table = slots if slots is not None else TICKER_SLOT
    idx = table.get(key)
    if idx is None:
        idx = sum(map(ord, key)) % len(p["series"])
    return p["series"][idx % len(p["series"])]


MARGIN_T, MARGIN_B = 56, 88


def apply(fig, mode: str = "light", height: int = 440):
    """공통 레이아웃. 그리드/축은 후퇴시키고 데이터가 앞에 오게 한다.

    범례는 항상 하단이다. 상단(y=1.02)에 두면 제목과 같은 줄을 다퉈 겹친다.
    차트 높이가 제각각이므로 범례 위치를 픽셀 기준으로 환산해 배치한다.
    """
    p = palette(mode)
    plot_h = max(height - MARGIN_T - MARGIN_B, 120)
    legend_y = -(46.0 / plot_h)

    fig.update_layout(
        height=height,
        paper_bgcolor=p["surface"],
        plot_bgcolor=p["surface"],
        font=dict(family=FONT, color=p["text_secondary"], size=12),
        title_font=dict(family=FONT, color=p["text"], size=16),
        # xref="paper" 라야 제목이 y축과 같은 선에서 시작한다.
        # 기본값 "container" 는 x=0 이 도화지 끝이라 첫 글자가 잘린다.
        title=dict(x=0, xref="paper", xanchor="left"),
        margin=dict(l=64, r=28, t=MARGIN_T, b=MARGIN_B),
        hovermode="x unified",
        hoverlabel=dict(font_family=FONT, font_size=12),
        legend=dict(orientation="h", yanchor="top", y=legend_y,
                    xanchor="left", x=0, font=dict(color=p["text_secondary"])),
        showlegend=True,
    )
    fig.update_xaxes(showgrid=False, linecolor=p["axis"], ticks="outside",
                     tickcolor=p["axis"], tickfont=dict(color=p["muted"]),
                     showspikes=True, spikemode="across", spikesnap="cursor",
                     spikethickness=1, spikedash="dot", spikecolor=p["muted"])
    fig.update_yaxes(showgrid=True, gridcolor=p["grid"], gridwidth=1,
                     zeroline=False, linecolor=p["axis"],
                     tickfont=dict(color=p["muted"]))
    return fig
