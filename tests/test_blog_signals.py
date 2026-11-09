"""블로그 매매 규칙 신호 검증.

1) 오실레이터: 정의상 성질(범위, index 보존, 단조 추세 방향)
2) 재현: 실제 SOXL 데이터(fixtures/soxl_2024_2025.csv)에서 블로그 글의 매매일이 나오는가
   - 2025-04-04: 3중 웅덩이 O, OBV gold X   (블로그: "OBV가 아직 gold가 안 나서 1차만")
   - 2025-04-07: OBV gold O                 (블로그: "OBV gold까지 나서 더 큰 수량")
   - 2025-08-04: 주봉 매도 신호              (블로그 매도 2025-08-08, 같은 주)
   fixture 출처: Yahoo 일봉(분할 조정 종가) — 블로그 매수가 8.7 / 7.9 와 일치.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.indicators import oscillators as osc
from src.research import blog_signals as bs

FIX = Path(__file__).parent / "fixtures" / "soxl_2024_2025.csv"


@pytest.fixture(scope="module")
def ohlcv() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    idx = pd.bdate_range("2022-01-03", periods=400)
    c = pd.Series(50 + np.cumsum(rng.normal(0, 1, 400)), index=idx).clip(lower=5)
    spread = rng.uniform(0.2, 1.5, 400)
    return pd.DataFrame({"open": c.shift().bfill(), "high": c + spread,
                         "low": c - spread, "close": c,
                         "volume": rng.uniform(1e6, 2e6, 400)}, index=idx)


@pytest.fixture(scope="module")
def soxl() -> pd.DataFrame:
    return pd.read_csv(FIX, parse_dates=["date"]).set_index("date")


# --- 1. 오실레이터 성질 -------------------------------------------------------
def test_index_preserved(ohlcv):
    h, l, c, v = ohlcv.high, ohlcv.low, ohlcv.close, ohlcv.volume
    for out in [osc.stoch_slow(h, l, c), osc.cci(h, l, c), osc.dmi(h, l, c), osc.obv(c, v)]:
        assert out.index.equals(ohlcv.index)
    assert bs.features(ohlcv).index.equals(ohlcv.index)


def test_stoch_range(ohlcv):
    st = osc.stoch_slow(ohlcv.high, ohlcv.low, ohlcv.close).dropna()
    assert st.min().min() >= 0 and st.max().max() <= 100


def test_dmi_direction():
    idx = pd.bdate_range("2023-01-02", periods=80)
    up = pd.Series(np.linspace(10, 50, 80), index=idx)
    d = osc.dmi(up + 0.5, up - 0.5, up).dropna()
    assert (d.pdi > d.mdi).all()                        # 단조 상승 → PDI 우위
    dn = up[::-1].set_axis(idx)
    d2 = osc.dmi(dn + 0.5, dn - 0.5, dn).dropna()
    assert (d2.mdi > d2.pdi).all()


def test_obv_sign():
    idx = pd.bdate_range("2023-01-02", periods=4)
    c = pd.Series([10, 11, 10, 10], index=idx, dtype=float)
    v = pd.Series([5, 7, 3, 9], index=idx, dtype=float)
    assert osc.obv(c, v).tolist() == [0, 7, 4, 4]


def test_cci_manual(ohlcv):
    h, l, c = ohlcv.high, ohlcv.low, ohlcv.close
    tp = ((h + l + c) / 3).iloc[-20:]
    ref = (tp.iloc[-1] - tp.mean()) / (0.015 * np.abs(tp - tp.mean()).mean())
    assert osc.cci(h, l, c, 20).iloc[-1] == pytest.approx(ref)


# --- 2. 블로그 매매일 재현 -----------------------------------------------------
def test_blog_buy_dates(soxl):
    f = bs.features(soxl)
    assert f.loc["2025-04-04", "pit"]
    assert not f.loc["2025-04-04", "obv_gold"]
    assert f.loc["2025-04-07", "obv_gold"]
    assert f.loc["2025-04-03":"2025-04-04", "mouth_max"].any()   # 입 최대
    # 댓글: 웅덩이 + ADX 데드 '후' + 입 최대 → 4/4 매수 타이밍 (ADX 데드는 3/24~28 에 먼저)
    assert f.loc["2025-04-04", "mouth"]
    assert not f.loc["2025-04-04", "adx_dead"]                 # 당일에는 ADX 가 오르는 중
    # 3단계는 2단계(DMI) 셋업 뒤의 DI 골든크로스: 5/8
    s = bs.signals(soxl)
    assert s.loc["2025-04-11":"2025-06-30", "di_gold"][lambda x: x].index.tolist() == [
        pd.Timestamp("2025-05-08")]
    assert f.loc["2025-04-04", "close"] == pytest.approx(8.73)


def test_blog_sell_week(soxl):
    s = bs.daily_sell(soxl)
    hits = s.loc["2025-07-21":"2025-08-15"]
    assert hits[hits].index.tolist() == [pd.Timestamp("2025-08-04")]


def test_no_lookahead_in_weekly_sell(soxl):
    """주봉 신호는 그 주가 끝난 다음 거래일에만 나타나야 한다."""
    s = bs.daily_sell(soxl)
    for day in s[s].index:
        prev = soxl.index[soxl.index < day][-1]          # 직전 거래일은 지난주여야 한다
        assert prev.isocalendar()[:2] != day.isocalendar()[:2]


def test_signals_frame(soxl):
    s = bs.signals(soxl)
    assert list(s.columns) == [k for k, *_ in bs.STAGES]
    assert s.dtypes.eq(bool).all()


def test_event_study_runs(soxl):
    es = bs.event_study(soxl, start="2024-03-01", n_perm=50)
    assert len(es) == 4 and (es["N"] >= 0).all()          # 1, 2(DMI), 2(OBV), 3


def test_episodes_cooldown():
    idx = pd.bdate_range("2023-01-02", periods=50)
    s = pd.Series(False, index=idx)
    s.iloc[[0, 1, 2, 30, 31]] = True
    assert bs.episodes(s, 20)[lambda x: x].index.tolist() == [idx[0], idx[30]]


# --- 3. 텔레그램 알림 규칙 -----------------------------------------------------
def test_alert_on_blog_buy_day(soxl):
    from src.alerts import rules
    al = rules.blog_signals(soxl.loc[:"2025-04-07"], "SOXL", "YF.SOXL", provisional=True)
    keys = {a.key for a in al}
    assert {"blog:YF.SOXL:pit", "blog:YF.SOXL:obv_gold"} <= keys
    assert all("잠정" in a.title for a in al)
    # Telegram legacy Markdown 을 깨는 문자가 본문에 없어야 한다
    for a in al:
        assert not any(ch in a.body for ch in "_*[`")


def test_alert_quiet_in_uptrend(soxl):
    from src.alerts import rules
    al = rules.blog_signals(soxl.loc[:"2025-07-15"], "SOXL", "YF.SOXL")
    assert not [a for a in al if a.key.endswith((":pit", ":obv_gold"))]


def test_us_market_open():
    from src.alerts.run import us_market_open
    ts = lambda s: pd.Timestamp(s, tz="America/New_York")
    assert us_market_open(ts("2026-11-09 10:00"))          # 월요일 장중
    assert not us_market_open(ts("2026-11-09 09:00"))      # 개장 전
    assert not us_market_open(ts("2026-11-09 16:30"))      # 마감 후
    assert not us_market_open(ts("2026-11-08 12:00"))      # 일요일


def test_recent_events(soxl):
    ev = bs.recent_events(soxl.loc[:"2025-04-30"], days=60)
    assert {"pit", "obv_gold"} <= set(ev["key"])
    assert ev["date"].is_monotonic_decreasing
    assert set(ev["signal"]) <= {"매수 준비", "저점 매수", "확인 매수", "매도 신호"}


def test_compare_entries(soxl):
    r = bs.compare_entries(soxl, start="2024-03-01", holds=(20,))
    assert len(r["table"]) == 3 and r["table"].index[0].startswith("저점 매수")
    assert {"N", "20일 중앙", "20일 승률"} <= set(r["table"].columns)
    assert list(r["pairs"].columns) == ["저점 매수일", "확인 매수일", "확인 매수가 더 비싼 정도"]


# --- 4. AND·순서 의미 (합성 bool 입력으로 stage_flags 직접 검증) -------------------
def _flags(n=80, **on):
    """BASE_FLAGS 전부 False 인 프레임에 on={'col': [위치들]} 만 True 로."""
    idx = pd.bdate_range("2024-01-01", periods=n)
    f = pd.DataFrame(False, index=idx, columns=bs.BASE_FLAGS)
    for col, pos in on.items():
        f.iloc[pos, f.columns.get_loc(col)] = True
    return f


OS3 = dict(os_stoch=[40], os_rsi=[40], os_cci=[40])          # 40일째 과매도 3종


def test_pit_needs_all_three():
    assert bs.stage_flags(_flags(**OS3))["pit"].iloc[40]
    for drop in OS3:                                          # 하나라도 빠지면 아님 (AND)
        st = bs.stage_flags(_flags(**{k: v for k, v in OS3.items() if k != drop}))
        assert not st["pit"].any()


def test_pit_window_3_days():
    st = bs.stage_flags(_flags(os_stoch=[38], os_rsi=[39], os_cci=[40]))
    assert st["pit"].iloc[40]                                 # 3일 안에 흩어져도 인정
    st = bs.stage_flags(_flags(os_stoch=[36], os_rsi=[39], os_cci=[40]))
    assert not st["pit"].iloc[40]                             # 4일 전은 창 밖


def test_mouth_requires_prior_adx_dead():
    base = dict(**OS3, mouth_max=[40])
    assert not bs.stage_flags(_flags(**base))["mouth"].any()                    # ADX 데드 없음
    assert not bs.stage_flags(_flags(**base, adx_dead=[40]))["mouth"].iloc[40]  # 같은 날만 → 아님
    assert bs.stage_flags(_flags(**base, adx_dead=[33]))["mouth"].iloc[40]      # 7일 전 → 인정
    assert bs.stage_flags(_flags(**base, adx_dead=[25]))["mouth"].iloc[40]      # 15일 전 → 경계 안
    assert not bs.stage_flags(_flags(**base, adx_dead=[24]))["mouth"].iloc[40]  # 16일 전 → 창 밖
    assert not bs.stage_flags(_flags(mouth_max=[40], adx_dead=[33]))["mouth"].any()  # 과매도 없음


def test_obv_gold_requires_pit():
    assert bs.stage_flags(_flags(**OS3, obv_cross=[41]))["obv_gold"].iloc[41]
    assert not bs.stage_flags(_flags(obv_cross=[41]))["obv_gold"].any()


def test_di_gold_requires_dmi_setup_within_window():
    setup = dict(**OS3, mouth_max=[40], adx_dead=[33])        # 40일째 2단계(DMI)
    st = bs.stage_flags(_flags(**setup))
    # 과매도·입 최대는 3일 창이라 2단계가 40~42일 켜져 있다 → 30일은 마지막 날(42)부터 센다
    assert st["mouth"][st["mouth"]].index.tolist() == list(st.index[40:43])
    assert bs.stage_flags(_flags(**setup, di_cross=[60]))["di_gold"].iloc[60]       # 18일 뒤
    assert bs.stage_flags(_flags(**setup, di_cross=[72]))["di_gold"].iloc[72]       # 30일 뒤(경계)
    assert not bs.stage_flags(_flags(**setup, di_cross=[73]))["di_gold"].any()      # 31일 뒤
    assert not bs.stage_flags(_flags(di_cross=[60]))["di_gold"].any()               # 셋업 없음
    # OBV 2단계만 있으면 기본(di_setup="mouth")에선 3단계 아님, 변형에선 인정
    obv_only = dict(**OS3, obv_cross=[41], di_cross=[55])
    assert not bs.stage_flags(_flags(**obv_only))["di_gold"].any()
    p = bs.Params(di_setup="mouth_or_obv")
    assert bs.stage_flags(_flags(**obv_only), p)["di_gold"].iloc[55]


def test_di_gold_obv_reference_path():
    """참고 신호: 2단계(OBV) 뒤 30거래일 안 DI 골든크로스, 단 정식 3단계(DMI 경로)와 겹치지 않음."""
    obv = dict(**OS3, obv_cross=[41])                         # obv_gold 41~42일(과매도 창 끝 42)
    st = bs.stage_flags(_flags(**obv))
    assert st["obv_gold"][st["obv_gold"]].index.tolist() == list(st.index[41:43])
    assert bs.stage_flags(_flags(**obv, di_cross=[55]))["di_gold_obv"].iloc[55]
    assert bs.stage_flags(_flags(**obv, di_cross=[72]))["di_gold_obv"].iloc[72]    # 마지막 날+30
    assert not bs.stage_flags(_flags(**obv, di_cross=[73]))["di_gold_obv"].any()   # 창 밖
    assert not bs.stage_flags(_flags(di_cross=[55]))["di_gold_obv"].any()          # 셋업 없음
    # DMI 셋업도 있으면 정식 3단계가 되고 참고 신호는 꺼진다 (같은 날 두 번 알리지 않음)
    both = bs.stage_flags(_flags(**obv, mouth_max=[40], adx_dead=[33], di_cross=[55]))
    assert both["di_gold"].iloc[55] and not both["di_gold_obv"].any()


def test_reference_signal_is_opt_in(soxl):
    """ref 를 요청하지 않으면 기존 신호 표·알림이 그대로다."""
    from src.alerts import rules
    base = bs.signals(soxl)
    with_ref = bs.signals(soxl, ref=["di_gold_obv"])
    assert list(base.columns) == [k for k, *_ in bs.STAGES]
    assert list(with_ref.columns) == list(base.columns) + ["di_gold_obv"]
    assert with_ref[base.columns].equals(base)
    al = rules.blog_signals(soxl, "SOXL", "YF.SOXL")
    assert not [a for a in al if a.key.endswith(":di_gold_obv")]
    assert bs.SIGNAL["di_gold_obv"][0] == "확인 매수(참고)" and "참고" in bs.LABEL["di_gold_obv"]


def test_reference_quiet_when_dmi_path(soxl):
    """2025-04 는 정식 3단계(DMI 경로) 국면 — ref 를 켜도 참고 신호가 따로 나면 안 된다."""
    from src.alerts import rules
    full = bs.signals(soxl, ref=["di_gold_obv"]).loc["2025-04-01":"2025-06-30"]
    assert full["di_gold"].any() and not full["di_gold_obv"].any()
    keys = {a.key for a in rules.blog_signals(soxl.loc[:"2025-04-07"], "T", "YF.T",
                                              ref=["di_gold_obv"])}
    assert "blog:YF.T:obv_gold" in keys and "blog:YF.T:di_gold_obv" not in keys


def test_reference_alert_wiring(soxl, monkeypatch):
    """참고 신호가 켜지면 알림 key·제목·레벨이 정해진 형태로 나간다."""
    from src.alerts import rules
    real = bs.stage_now

    def fake(df, p=bs.Params(), recent=3, ref=()):
        out = real(df, p, recent, ref)
        out["on"]["di_gold_obv"] = "di_gold_obv" in ref
        return out

    monkeypatch.setattr(bs, "stage_now", fake)
    al = {a.key: a for a in rules.blog_signals(soxl, "TQQQ", "YF.TQQQ", ref=["di_gold_obv"])}
    a = al["blog:YF.TQQQ:di_gold_obv"]
    assert a.level == "warn" and "확인 매수(참고)" in a.title and "참고" in a.body
    assert not any(ch in a.body for ch in "_*[`")
    assert "blog:YF.TQQQ:di_gold_obv" not in {
        x.key for x in rules.blog_signals(soxl, "SOXL", "YF.SOXL")}


# --- 5. 미래 데이터 미사용 (look-ahead) -------------------------------------------
def test_no_lookahead_daily_signals(soxl):
    """t 까지만 준 데이터로 계산한 t 의 신호 == 전체 데이터로 계산한 t 의 신호."""
    full = bs.signals(soxl)
    days = soxl.loc["2024-09-01":].index[::5]
    for t in days:
        part = bs.signals(soxl.loc[:t]).loc[t]
        assert part.equals(full.loc[t]), (t, part.to_dict(), full.loc[t].to_dict())


# --- 6. 알림 중복 방지 (일별 시뮬레이션) -----------------------------------------
def test_alert_fires_once_per_episode(soxl):
    """2025-03~06 를 하루씩 진행하며 run.py 와 같은 '새 key 만 전송' 규칙을 적용."""
    from src.alerts import rules
    prev, fired = set(), {}
    for t in soxl.loc["2025-03-17":"2025-06-30"].index:
        active = {a.key for a in rules.blog_signals(soxl.loc[:t], "SOXL", "YF.SOXL")}
        for k in active - prev:
            fired.setdefault(k.split(":")[-1], []).append(t)
        prev = active
    first = {k: v[0] for k, v in fired.items()}
    assert first["pit"] == pd.Timestamp("2025-04-03")
    assert first["mouth"] == pd.Timestamp("2025-04-03")
    assert first["obv_gold"] == pd.Timestamp("2025-04-07")
    assert first["di_gold"] == pd.Timestamp("2025-05-08")
    for k, v in fired.items():                                # 같은 국면에 반복 전송 없음
        assert len(v) == 1, (k, v)


# --- 7. 기준값 근처에서 블로그 매매 재현이 유지되는가 ------------------------------
@pytest.mark.parametrize("adx_win,close_win", [(10, 25), (10, 45), (15, 30), (20, 25), (20, 45)])
def test_blog_reproduction_robust(soxl, adx_win, close_win):
    p = bs.Params(adx_prior_win=adx_win, close_win=close_win)
    s = bs.signals(soxl, p)
    assert s.loc["2025-04-04", "mouth"] and s.loc["2025-04-07", "obv_gold"]
    di = s.loc["2025-04-10":"2025-06-30", "di_gold"]
    assert di[di].index[0] == pd.Timestamp("2025-05-08")


def test_default_params_pinned():
    """검증 문서와 화면 설명이 이 값들을 전제로 한다 — 바꾸면 문서·화면도 같이 고칠 것."""
    p = bs.Params()
    assert (p.adx_prior_win, p.close_win, p.di_setup, p.adx_dead_mode, p.pit_win) == \
        (15, 30, "mouth", "sig", 3)
