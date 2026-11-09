"""'현세의 주식' 블로그 매매 규칙 — 레버리지 ETF(SOXL/TQQQ) 바닥 매수 신호.

출처
- 본문(2025-08-19): Stochastic·RSI 과매도 '웅덩이' → 1차 매수, OBV gold → 2차 매수,
  Stochastic 상단 웅덩이 + OBV 이격 → 매도
- 블로그 주인 댓글(2026-11-04): Stoch·CCI·RSI 웅덩이 + ADX 데드 후 PDI·MDI 입 최대 → 매수,
  입을 닫으며 gold 날 때 → 더 확실한 매수

원문의 조건은 모두 AND 다(연결어미 '~고', '~후', '~까지', '~나자마자'). 댓글의 두 매수 시점
('입 최대' 와 '더 확실히 → gold')은 같은 셋업 위에서 언제 들어갈지의 선택이다.
본문의 OBV 조건과 댓글의 ADX·DMI 조건을 함께 요구하는지는 원문에 없어 2단계를 두 갈래로 둔다.

정량화(원문은 수치가 없어 해석이 들어간다 — 대시보드에 그대로 노출한다)
- 1  과매도   : 최근 3일 안에 Slow %K(5,3,3)<20, RSI(14)<30, CCI(20)<-100 모두 발생
- 2  DMI      : 1 + 직전 15거래일 안 ADX 데드(ADX<9일 평균) + MDI−PDI 60일 최대(최근 3일) — 댓글 '매수타이밍'
- 2' OBV      : 1 + OBV 가 9일 SMA 상향 돌파(최근 3일) — 본문 2차 매수
- 3  DI 골든  : 2(DMI) 이후 30거래일 안에 PDI 가 MDI 상향 돌파 — 댓글 '더 확실히'
- 3' 참고     : 2(OBV) 이후 30거래일 안 DI 골든인데 3 이 아닌 경우 — 2(DMI) 없이 반등한 국면.
                검증에서 채택한 3단계는 DMI 경로뿐이라 '참고' 신호로만 둔다. 종목별로 켠다
                (series.yaml 의 ref_signals). 계산은 항상 하되 signals/stage_now 는 ref 로 요청할 때만 낸다.
- 매도        : 주봉 Slow(10,5,5) 과매수(>80) 후 K<D, 그리고 OBV−signal 이격 축소

2025-04 확인: ADX 데드 3/24~28 → 과매도·입 최대 4/3~4 (블로그 1차 매수 4/4) → OBV 골든 4/7(2차)
→ DI 골든 5월 초. ADX 데드는 입 최대 '전에' 나온다 — 같은 날 조건으로 두면 이 매수일이 안 잡힌다.

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
    adx_prior_win: int = 15          # 'ADX 가 데드난 후' — 입 최대 직전 몇 거래일 안에 데드가 있었나
    adx_dead_mode: str = "sig"       # "sig": ADX < 자기 9일 평균 / "turn": ADX 가 전일보다 하락
    di_setup: str = "mouth"          # 3단계 셋업: "mouth"(2단계 DMI) / "mouth_or_obv"(2단계 둘 중 하나)
    weekly_stoch: tuple[int, int, int] = (10, 5, 5)


#: 신호 단계 — (컬럼, 라벨, 설명). 대시보드·알림이 같은 정의를 쓴다.
STAGES: list[tuple[str, str, str]] = [
    ("pit", "1단계 과매도",
     "Stoch·CCI·RSI 동시 과매도. 블로그 1차 매수. 단독으로는 '떨어지는 칼'일 수 있음"),
    ("mouth", "2단계 DMI 입 최대",
     "과매도 + 직전 ADX 데드 + MDI−PDI 입 최대. 댓글의 매수 타이밍"),
    ("obv_gold", "2단계 OBV 골든크로스",
     "과매도 + OBV 골든크로스. 거래량이 돌아섬. 블로그 본문 2차(본격) 매수"),
    ("di_gold", "3단계 DI 골든크로스",
     "DMI 2단계 이후 입이 닫히며 PDI가 MDI를 상향 돌파. 댓글의 '더 확실한' 매수"),
    ("sell_w", "매도 신호",
     "주봉 Stoch 과매수 후 꺾임 + OBV 이격 축소. 블로그 매도 규칙"),
]
#: 참고 신호 — 기본 단계에 넣지 않고 종목별로 켠다(series.yaml ref_signals). 성과 통계·비교표에는 안 들어간다.
REF_STAGES: list[tuple[str, str, str]] = [
    ("di_gold_obv", "3단계 DI 골든크로스 (OBV 경로·참고)",
     "2단계(OBV) 뒤 30거래일 안 PDI가 MDI를 상향 돌파. 2단계(DMI) 없이 반등한 국면이라 "
     "검증에서 채택한 3단계는 아님 — 참고용"),
]
REF_KEYS = [k for k, *_ in REF_STAGES]
LABEL = {k: lab for k, lab, _ in STAGES + REF_STAGES}

#: 단계 → 사람이 읽는 신호 이름과 색. 사이트·텔레그램이 같은 말을 쓴다.
SIGNAL: dict[str | None, tuple[str, str]] = {
    None: ("신호 없음", "gray"),
    "pit": ("매수 준비", "orange"),
    # 2단계는 바닥 근처에서 먼저 사는 신호, 3단계는 반등을 확인하고 사는 신호다.
    # 사례 검증(2011~): 같은 하락 국면에서 3단계 매수가가 2단계보다 대개 비쌌고(SOXL 8쌍 중 7, TQQQ 6),
    # 2단계는 드물고 매수 뒤 흔들림이 더 컸다.
    "mouth": ("저점 매수", "green"),
    "obv_gold": ("저점 매수", "green"),
    "di_gold": ("확인 매수", "blue"),
    "di_gold_obv": ("확인 매수(참고)", "blue"),
    "sell_w": ("매도 신호", "red"),
}


def _ref(ref) -> list[str]:
    """요청된 참고 신호 키만, REF_KEYS 순서로."""
    ref = set(ref or ())
    return [k for k in REF_KEYS if k in ref]


def recent_events(df: pd.DataFrame, p: Params = Params(), days: int = 365,
                  cooldown: int = 20, ref=()) -> pd.DataFrame:
    """최근 days 일 신호 이벤트(에피소드 첫날) + 신호 이후 현재까지 수익률. 최신순.

    텔레그램 알림과 같은 단계 정의를 쓴다(알림은 '새로 켜질 때' 기준이라 날짜가 하루 이틀 다를 수 있다).
    """
    d = _ohlcv(df)
    sig = signals(d, p, ref=ref)
    start = d.index[-1] - pd.Timedelta(days=days)
    rows = []
    for key, label, _ in STAGES + [s_ for s_ in REF_STAGES if s_[0] in sig]:
        ep = episodes(sig[key], cooldown).loc[start:]
        for day in ep[ep].index:
            rows.append({"date": day, "key": key, "stage": label,
                         "signal": SIGNAL[key][0], "close": float(d.close.loc[day]),
                         "since": float(d.close.iloc[-1] / d.close.loc[day] - 1)})
    out = pd.DataFrame(rows, columns=["date", "key", "stage", "signal", "close", "since"])
    return out.sort_values("date", ascending=False).reset_index(drop=True)


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

    f["os_stoch"] = f["sk"] < p.os_stoch
    f["os_rsi"] = f["rsi"] < p.os_rsi
    f["os_cci"] = f["cci"] < p.os_cci
    f["obv_cross"] = osc.cross_up(f["obv"], f["obv_sig"])
    f["obv_above"] = f["obv"] > f["obv_sig"]
    smax = f["spread"].rolling(p.spread_win).max()
    f["mouth_max"] = (f["spread"] >= smax - 1e-9) & (f["spread"] > 0)
    f["mouth_recent"] = _recent(f["mouth_max"], p.spread_recent)
    f["adx_dead"] = ((f["adx"] < f["adx_sig"]) if p.adx_dead_mode == "sig"
                     else (f["adx"] < f["adx"].shift()))
    f["di_cross"] = osc.cross_up(f["pdi"], f["mdi"])
    pos = np.arange(len(f), dtype=float)
    last = pd.Series(np.where(f["mouth_max"], pos, np.nan), index=f.index).ffill()
    f["days_since_mouth"] = pos - last

    st_ = stage_flags(f, p)
    for col in st_.columns:
        f[col] = st_[col]
    return f


#: stage_flags 가 읽는 기초 조건 컬럼(모두 bool)
BASE_FLAGS = ["os_stoch", "os_rsi", "os_cci", "obv_cross", "mouth_max", "adx_dead", "di_cross"]


def stage_flags(f: pd.DataFrame, p: Params = Params()) -> pd.DataFrame:
    """기초 조건(bool 컬럼, BASE_FLAGS) → 단계 판정. 원문의 AND·순서 구조를 여기서만 정의한다.

    - pit      : 과매도 3종이 최근 pit_win 일 안에 모두
    - obv_gold : pit AND 최근 pit_win 일 안 OBV 골든크로스              (본문 2차 매수)
    - mouth    : pit AND 최근 pit_win 일 안 입 최대 AND 직전 adx_prior_win 일 안 ADX 데드
                 — '데드난 후' 이므로 오늘의 데드는 세지 않는다            (댓글 매수타이밍)
    - di_gold  : DI 골든크로스 AND 셋업(기본 mouth)이 마지막으로 켜져 있던 날부터
                 close_win 거래일 이내                                      (댓글 '더 확실히')
    - di_gold_obv (참고): DI 골든크로스 AND obv_gold 가 마지막으로 켜져 있던 날부터
                 close_win 거래일 이내 AND di_gold 아님                      (DMI 2단계 없는 반등)
    """
    w = p.pit_win
    b = {k: f[k].fillna(False).astype(bool) for k in BASE_FLAGS}
    out = pd.DataFrame(index=f.index)
    out["pit"] = _recent(b["os_stoch"], w) & _recent(b["os_rsi"], w) & _recent(b["os_cci"], w)
    out["obv_gold"] = out["pit"] & _recent(b["obv_cross"], w)
    out["adx_dead_prior"] = _recent(b["adx_dead"].shift(1, fill_value=False), p.adx_prior_win)
    out["mouth"] = out["pit"] & _recent(b["mouth_max"], w) & out["adx_dead_prior"]
    setup = out["mouth"] if p.di_setup == "mouth" else (out["mouth"] | out["obv_gold"])
    pos = np.arange(len(f), dtype=float)
    last_setup = pd.Series(np.where(setup, pos, np.nan), index=f.index).ffill()
    out["days_since_setup"] = pos - last_setup
    # 3단계는 독립 조건이 아니다 — '입을 벌리다가 닫으러 가면서' 는 2단계(DMI) 의 그 입이다
    out["di_gold"] = b["di_cross"] & (out["days_since_setup"] <= p.close_win)
    last_obv = pd.Series(np.where(out["obv_gold"], pos, np.nan), index=f.index).ffill()
    out["days_since_obv"] = pos - last_obv
    out["di_gold_obv"] = (b["di_cross"] & (out["days_since_obv"] <= p.close_win)
                          & ~out["di_gold"])
    return out


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


def signals(df: pd.DataFrame, p: Params = Params(), ref=()) -> pd.DataFrame:
    """단계별 신호 bool 테이블 (STAGES 순서). ref 로 요청한 참고 신호는 맨 뒤에 붙는다."""
    f = features(df, p)
    out = f[["pit", "mouth", "obv_gold", "di_gold"]].copy()
    out["sell_w"] = daily_sell(df, p)
    for k in _ref(ref):
        out[k] = f[k]
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
        ("DMI 입 최대 (MDI−PDI)", bool(r.mouth_recent),
         f"MDI−PDI {r.spread:.1f} (60일 최대 {f['spread'].tail(p.spread_win).max():.1f})"),
        ("ADX 데드 (직전 15일 안)", bool(r.adx_dead_prior),
         f"ADX {r.adx:.1f} vs 신호선 {r.adx_sig:.1f}"),
        ("OBV 골든크로스", bool(_recent(f["obv_cross"], p.pit_win).iloc[-1]),
         ("최근 3일 안 신호선 상향 돌파" if bool(_recent(f["obv_cross"], p.pit_win).iloc[-1])
          else "교차 없음") + f" · 현재 신호선 {'위' if r.obv_above else '아래'}"),
        ("DI 골든크로스 (PDI>MDI)", bool(r.di_gold) or bool(r.pdi > r.mdi),
         f"PDI {r.pdi:.1f} / MDI {r.mdi:.1f}"),
        ("주봉 Stoch 과매도", bool(w.sk < p.os_stoch),
         f"주봉 %K {w.sk:.1f}, RSI {w.rsi:.1f}" + (" · 진행 중 주" if w.partial else "")),
    ]
    return [{"name": n, "ok": ok, "detail": det} for n, ok, det in rows]


def stage_now(df: pd.DataFrame, p: Params = Params(), recent: int = 3, ref=()) -> dict:
    """현재 단계 요약: 최근 recent 거래일 안에 켜진 가장 높은 단계."""
    s = signals(df, p, ref=ref).tail(recent)
    on = {k: bool(s[k].any()) for k in s.columns}
    if on.get("sell_w"):
        level = "sell_w"
    elif on.get("di_gold"):
        level = "di_gold"
    elif on.get("di_gold_obv"):
        level = "di_gold_obv"
    elif on.get("mouth"):
        level = "mouth"
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
    for key, label, _ in [s_ for s_ in STAGES if s_[0] != "sell_w"]:
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


def compare_entries(df: pd.DataFrame, p: Params = Params(), start: str = "2011-01-01",
                    holds=(60, 120), cooldown: int = 20) -> dict:
    """저점 매수(2단계 DMI·OBV) vs 확인 매수(3단계) — 같은 조건(다음날 시가 매수, h거래일 보유)의
    거래 성과와, 같은 국면 짝(2단계 DMI 뒤 60거래일 안에 3단계)의 매수가 차이.

    반환: {"table": 단계별 요약 DataFrame, "pairs": 짝 DataFrame}
    """
    d = _ohlcv(df)
    sig = signals(d, p).loc[start:]
    dd = d.loc[start:]
    o, c, n = dd.open.to_numpy(), dd.close.to_numpy(), len(dd)
    rows = []
    for key in ("mouth", "obv_gold", "di_gold"):
        idx = np.flatnonzero(episodes(sig[key], cooldown).to_numpy())
        row = {"신호": f"{SIGNAL[key][0]} · {LABEL[key]}"}
        for h in holds:
            r = np.array([c[i + 1 + h] / o[i + 1] - 1 for i in idx if i + 1 + h < n])
            mae = np.array([c[i + 1:i + 2 + h].min() / o[i + 1] - 1 for i in idx if i + 1 + h < n])
            row["N"] = len(r)
            row[f"{h}일 중앙"] = float(np.median(r)) if len(r) else np.nan
            row[f"{h}일 승률"] = float((r > 0).mean()) if len(r) else np.nan
            row[f"{h}일 최악"] = float(r.min()) if len(r) else np.nan
            if h == holds[0]:
                row["보유 중 최대하락(중앙)"] = float(np.median(mae)) if len(mae) else np.nan
        rows.append(row)
    ep2 = episodes(sig["mouth"], cooldown)
    pairs = []
    for i in np.flatnonzero(ep2.to_numpy()):
        later = np.flatnonzero(sig["di_gold"].to_numpy()[i + 1:i + 61])
        if len(later) and i + 1 < n:
            j = i + 1 + later[0]
            if j + 1 < n:
                pairs.append({"저점 매수일": dd.index[i], "확인 매수일": dd.index[j],
                              "확인 매수가 더 비싼 정도": o[j + 1] / o[i + 1] - 1})
    return {"table": pd.DataFrame(rows).set_index("신호"),
            "pairs": pd.DataFrame(pairs, columns=["저점 매수일", "확인 매수일", "확인 매수가 더 비싼 정도"])}
