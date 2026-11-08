"""'현세의 주식' 블로그 매매 규칙 — 레버리지 ETF(SOXL/TQQQ) 바닥 매수 신호.

출처
- 본문(2025-08-19): Stochastic·RSI 과매도 '웅덩이' → 1차 매수, OBV gold → 2차 매수,
  Stochastic 상단 웅덩이 + OBV 이격 → 매도
- 블로그 주인 댓글(2026-11-04): Stoch·CCI·RSI 웅덩이 + ADX 데드 후 PDI·MDI 입 최대 → 매수,
  입을 닫으며 gold 날 때 → 더 확실한 매수

정량화(원문은 수치가 없어 해석이 들어간다 — 대시보드에 그대로 노출한다)
- 웅덩이  : 최근 3일 안에 Slow %K(5,3,3)<20, RSI(14)<30, CCI(20)<-100 모두 발생
- 입 최대 : MDI−PDI 가 최근 60일 최댓값 (MDI 우위), 최근 10일 안
- OBV gold: OBV 가 9일 SMA 상향 돌파 (웅덩이 중, 최근 3일 안)
- DI gold : 입 최대 이후 30거래일 안에 PDI 가 MDI 상향 돌파
- 매도    : 주봉 Slow(10,5,5) 과매수(>80) 후 K<D, 그리고 OBV−signal 이격 축소

재현 확인(SOXL): 2025-04-04 웅덩이·OBV gold 없음 → 4/7 OBV gold (블로그 1·2차 매수일과 일치),
주봉 매도 2025-08-04 (블로그 매도 8/8 과 같은 주).

모든 함수는 순수함수다. 입력 OHLCV DataFrame(index=date) 의 index 를 보존한다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.indicators import oscillators as osc
from src.indicators import technical as ta


@dataclass(frozen=True)
class Params:
    stoch: tuple[int, int, int] = (5, 3, 3)
    rsi_n: int = 14
    cci_n: int = 20
    dmi_n: int = 14
    obv_sig: int = 9
    adx_sig: int = 9
    os_stoch: float = 20.0
    os_rsi: float = 30.0
    os_cci: float = -100.0
    ob_stoch: float = 80.0
    pit_win: int = 3
    spread_win: int = 60
    spread_recent: int = 10
    close_win: int = 30
    weekly_stoch: tuple[int, int, int] = (10, 5, 5)


#: 신호 단계 — (컬럼, 라벨, 설명). 대시보드·알림이 같은 정의를 쓴다.
STAGES: list[tuple[str, str, str]] = [
    ("pit", "1단계 웅덩이",
     "Stoch·CCI·RSI 동시 과매도. 블로그 1차 매수. 단독으로는 '떨어지는 칼'일 수 있음"),
    ("obv_gold", "2단계 OBV gold",
     "과매도 중 거래량이 돌아섬. 블로그 2차(본격) 매수"),
    ("di_gold", "3단계 DI gold",
     "MDI−PDI 입 최대 이후 PDI가 MDI를 상향 돌파. 댓글의 '더 확실한' 매수"),
    ("sell_w", "매도 신호",
     "주봉 Stoch 과매수 후 꺾임 + OBV 이격 축소. 블로그 매도 규칙"),
]
LABEL = {k: lab for k, lab, _ in STAGES}


def _ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    return df[["open", "high", "low", "close", "volume"]].astype("float64").sort_index()


def _recent(s: pd.Series, w: int) -> pd.Series:
    return s.fillna(False).astype(float).rolling(w, min_periods=1).max().astype(bool)


def features(df: pd.DataFrame, p: Params = Params()) -> pd.DataFrame:
    """일봉 OHLCV → 지표 + 단계별 조건(bool)."""
    d = _ohlcv(df)
    h, l, c, v = d.high, d.low, d.close, d.volume
    f = d.copy()
    st = osc.stoch_slow(h, l, c, *p.stoch)
    f["sk"], f["sd"] = st["k"], st["d"]
    f["rsi"] = ta.rsi(c, p.rsi_n)
    f["cci"] = osc.cci(h, l, c, p.cci_n)
    dm = osc.dmi(h, l, c, p.dmi_n)
    f["pdi"], f["mdi"], f["adx"] = dm["pdi"], dm["mdi"], dm["adx"]
    f["adx_sig"] = f["adx"].rolling(p.adx_sig).mean()
    f["obv"] = osc.obv(c, v)
    f["obv_sig"] = f["obv"].rolling(p.obv_sig).mean()
    f["spread"] = f["mdi"] - f["pdi"]

    w = p.pit_win
    f["os_stoch"] = f["sk"] < p.os_stoch
    f["os_rsi"] = f["rsi"] < p.os_rsi
    f["os_cci"] = f["cci"] < p.os_cci
    f["pit"] = (_recent(f["os_stoch"], w) & _recent(f["os_rsi"], w)
                & _recent(f["os_cci"], w))

    f["obv_cross"] = osc.cross_up(f["obv"], f["obv_sig"])
    f["obv_above"] = f["obv"] > f["obv_sig"]
    f["obv_gold"] = f["pit"] & _recent(f["obv_cross"], w)

    smax = f["spread"].rolling(p.spread_win).max()
    f["mouth_max"] = (f["spread"] >= smax - 1e-9) & (f["spread"] > 0)
    f["mouth_recent"] = _recent(f["mouth_max"], p.spread_recent)
    f["adx_dead"] = f["adx"] < f["adx_sig"]

    pos = np.arange(len(f), dtype=float)
    last = pd.Series(np.where(f["mouth_max"], pos, np.nan), index=f.index).ffill()
    f["days_since_mouth"] = pos - last
    f["di_cross"] = osc.cross_up(f["pdi"], f["mdi"])
    f["di_gold"] = f["di_cross"] & (f["days_since_mouth"] <= p.close_win)
    return f


def weekly(df: pd.DataFrame, p: Params = Params()) -> pd.DataFrame:
    """주봉(금요일 마감) 매도 판정. 마지막 행은 진행 중인 주일 수 있다(partial 컬럼)."""
    d = _ohlcv(df)
    wk = d.resample("W-FRI").agg({"open": "first", "high": "max", "low": "min",
                                  "close": "last", "volume": "sum"}).dropna()
    last_day = d.index.max()
    st = osc.stoch_slow(wk.high, wk.low, wk.close, *p.weekly_stoch)
    wk["sk"], wk["sd"] = st["k"], st["d"]
    wk["rsi"] = ta.rsi(wk.close, p.rsi_n)
    wk["obv"] = osc.obv(wk.close, wk.volume)
    wk["obv_sig"] = wk["obv"].rolling(p.obv_sig).mean()
    gap = wk["obv"] - wk["obv_sig"]
    ob_recent = _recent(wk["sk"] > p.ob_stoch, 3)
    wk["sell_w"] = ob_recent & (wk["sk"] < wk["sd"]) & (gap < gap.shift(1))
    wk["partial"] = wk.index > last_day           # 이번 주 금요일이 아직 안 왔다
    return wk


def daily_sell(df: pd.DataFrame, p: Params = Params()) -> pd.Series:
    """주봉 매도 신호를 '확정 다음 거래일' 일봉 index 로 옮긴다(look-ahead 방지)."""
    d = _ohlcv(df)
    wk = weekly(d, p)
    out = pd.Series(False, index=d.index, name="sell_w")
    for day in wk.index[wk["sell_w"] & ~wk["partial"]]:
        nxt = d.index[d.index > day]
        if len(nxt):
            out.loc[nxt[0]] = True
    return out


def signals(df: pd.DataFrame, p: Params = Params()) -> pd.DataFrame:
    """단계별 신호 bool 테이블 (STAGES 순서)."""
    f = features(df, p)
    out = f[["pit", "obv_gold", "di_gold"]].copy()
    out["sell_w"] = daily_sell(df, p)
    return out.fillna(False).astype(bool)


def checklist(df: pd.DataFrame, p: Params = Params()) -> list[dict]:
    """현재(마지막 봉) 기준 규칙 체크리스트. 대시보드 상단 카드용."""
    f = features(df, p)
    wk = weekly(df, p)
    r, w = f.iloc[-1], wk.iloc[-1]
    rows = [
        ("Stoch 과매도", bool(_recent(f["os_stoch"], p.pit_win).iloc[-1]),
         f"Slow %K {r.sk:.1f} (<{p.os_stoch:.0f})"),
        ("RSI 과매도", bool(_recent(f["os_rsi"], p.pit_win).iloc[-1]),
         f"RSI {r.rsi:.1f} (<{p.os_rsi:.0f})"),
        ("CCI 과매도", bool(_recent(f["os_cci"], p.pit_win).iloc[-1]),
         f"CCI {r.cci:.0f} (<{p.os_cci:.0f})"),
        ("DMI 입 최대", bool(r.mouth_recent),
         f"MDI−PDI {r.spread:.1f} (60일 최대 {f['spread'].tail(p.spread_win).max():.1f})"),
        ("ADX 꺾임(데드)", bool(r.adx_dead),
         f"ADX {r.adx:.1f} vs 신호선 {r.adx_sig:.1f}"),
        ("OBV gold", bool(_recent(f["obv_cross"], p.pit_win).iloc[-1]),
         ("최근 3일 안 신호선 상향 돌파" if bool(_recent(f["obv_cross"], p.pit_win).iloc[-1])
          else "교차 없음") + f" · 현재 신호선 {'위' if r.obv_above else '아래'}"),
        ("DI gold (PDI>MDI)", bool(r.di_gold) or bool(r.pdi > r.mdi),
         f"PDI {r.pdi:.1f} / MDI {r.mdi:.1f}"),
        ("주봉 Stoch 과매도", bool(w.sk < p.os_stoch),
         f"주봉 %K {w.sk:.1f}, RSI {w.rsi:.1f}" + (" · 진행 중 주" if w.partial else "")),
    ]
    return [{"name": n, "ok": ok, "detail": det} for n, ok, det in rows]


def stage_now(df: pd.DataFrame, p: Params = Params(), recent: int = 3) -> dict:
    """현재 단계 요약: 최근 recent 거래일 안에 켜진 가장 높은 단계."""
    s = signals(df, p).tail(recent)
    on = {k: bool(s[k].any()) for k in s.columns}
    if on.get("sell_w"):
        level = "sell_w"
    elif on.get("di_gold"):
        level = "di_gold"
    elif on.get("obv_gold"):
        level = "obv_gold"
    elif on.get("pit"):
        level = "pit"
    else:
        level = None
    return {"level": level, "on": on, "asof": s.index[-1]}


# ------------------------------------------------------------------ 검증(이벤트 스터디)
def episodes(sig: pd.Series, cooldown: int = 20) -> pd.Series:
    """군집 신호를 에피소드 첫날로 압축. 독립 표본 수 과대평가 방지."""
    out, last = np.zeros(len(sig), dtype=bool), -10**9
    for i, v in enumerate(sig.fillna(False).to_numpy()):
        if v and i - last > cooldown:
            out[i], last = True, i
    return pd.Series(out, index=sig.index, name=sig.name)


def forward_returns(df: pd.DataFrame, horizons=(20, 60, 120)) -> pd.DataFrame:
    """신호일 다음날 시가 진입 기준 h거래일 후 종가 수익률 + 20일 MAE."""
    d = _ohlcv(df)
    entry = d.open.shift(-1)
    out = pd.DataFrame(index=d.index)
    for h in horizons:
        out[f"r{h}"] = d.close.shift(-h) / entry - 1.0
    low_min = d.low[::-1].rolling(20, min_periods=1).min()[::-1].shift(-1)
    out["mae20"] = low_min / entry - 1.0
    return out


def event_study(df: pd.DataFrame, p: Params = Params(), start: str = "2011-01-01",
                horizons=(20, 60, 120), n_perm: int = 1000,
                seed: int = 0) -> pd.DataFrame:
    """단계별 신호의 사후 성과 vs 무조건부(모든 거래일) 기준선.

    p_h: 같은 개수의 랜덤 진입일 표본 중앙값이 신호 표본 중앙값 이상일 확률.
    """
    d = _ohlcv(df)
    sig = signals(d, p).loc[start:]
    fwd = forward_returns(d, horizons).loc[start:]
    rng = np.random.default_rng(seed)
    rows = []
    for key, label, _ in STAGES[:3]:
        ep = episodes(sig[key])
        sub = fwd[ep]
        row = {"단계": label, "N": int(ep.sum())}
        for h in horizons:
            x = sub[f"r{h}"].dropna()
            base = fwd[f"r{h}"].dropna().to_numpy()
            row[f"{h}일 중앙값"] = float(x.median()) if len(x) else np.nan
            row[f"{h}일 상승확률"] = float((x > 0).mean()) if len(x) else np.nan
            row[f"{h}일 기준선"] = float(np.median(base)) if len(base) else np.nan
            if len(x) and len(base):
                perm = np.median(rng.choice(base, size=(n_perm, len(x))), axis=1)
                row[f"p{h}"] = float((perm >= x.median()).mean())
        row["20일 최대역행(중앙)"] = float(sub["mae20"].dropna().median()) if len(sub) else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def history(df: pd.DataFrame, p: Params = Params(), start: str = "2011-01-01",
            cooldown: int = 20) -> pd.DataFrame:
    """신호 발생 이력(에피소드 첫날) + 사후 성과. 최신순."""
    d = _ohlcv(df)
    sig = signals(d, p).loc[start:]
    fwd = forward_returns(d).loc[start:]
    frames = []
    for key, label, _ in STAGES:
        ep = episodes(sig[key], cooldown)
        if not ep.any():
            continue
        t = fwd[ep].copy()
        t.insert(0, "종가", d.close.reindex(t.index))
        t.insert(0, "신호", label)
        frames.append(t)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames).sort_index(ascending=False)
