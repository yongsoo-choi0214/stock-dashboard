"""알림 실행기 — 규칙 평가 → 새로 참이 된 것만 텔레그램 전송.

    python -m src.alerts.run              # 전송 (토큰 없으면 자동 dry-run)
    python -m src.alerts.run --dry-run    # 무조건 출력만
    python -m src.alerts.run --test       # 연결 확인용 메시지 1건 전송

토큰이 없으면 실패가 아니라 dry-run 이다. 알림은 부가 기능이고, 이것 때문에
데이터 갱신 워크플로가 죽으면 안 된다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import pandas as pd
import requests

from config.settings import DATA_DIR
from src import store
from src.alerts import rules
from src.alerts.rules import Alert
from src.indicators import liquidity as lq
from src.research import regime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

STATE = DATA_DIR / "_alerts.json"
API = "https://api.telegram.org/bot{token}/sendMessage"


# ------------------------------------------------------------------ 상태
def read_state() -> set[str]:
    if not STATE.exists():
        return set()
    try:
        return set(json.loads(STATE.read_text(encoding="utf-8")).get("active", []))
    except json.JSONDecodeError:
        return set()


def write_state(keys: set[str]) -> None:
    STATE.write_text(json.dumps(
        {"active": sorted(keys),
         "updated": pd.Timestamp.now(tz="Asia/Seoul").isoformat(timespec="seconds")},
        ensure_ascii=False, indent=2), encoding="utf-8")


# ------------------------------------------------------------------ 평가
def evaluate() -> list[Alert]:
    """현재 데이터에서 참인 모든 알림 상태."""
    macro, prices = store.read("macro"), store.read("prices")
    out: list[Alert] = []

    out += rules.data_staleness(store.read_meta())

    def ms(sid: str) -> pd.Series:
        s = macro[macro["series_id"] == sid].set_index("date")["value"]
        return s.sort_index().astype("float64")

    for ticker, name in [("KRX.1001", "KOSPI"), ("KRX.2001", "KOSDAQ")]:
        c = prices[prices["ticker"] == ticker].set_index("date")["close"].sort_index()
        if not c.empty:
            out += rules.rsi_extremes(c.astype("float64"), name, ticker)

    need = ["fred.WALCL", "fred.WTREGEN", "fred.RRPONTSYD"]
    if all(not ms(s).empty for s in need):
        nl = lq.us_net_liquidity(*[ms(s) for s in need])
        out += rules.liquidity_shock(nl)

        kospi = prices[prices["ticker"] == "KRX.1001"].set_index("date")["close"]
        if not kospi.empty:
            out += rules.regime_shift(
                regime.classify(nl, kospi.sort_index().astype("float64")))

    dep, mcap = ms("ecos.investor_deposit"), ms("ecos.kospi_marcap")
    if not dep.empty and not mcap.empty:
        out += rules.deposit_extreme(lq.deposit_ratio(dep, mcap.resample("ME").last()))

    return out


# ------------------------------------------------------------------ 전송
def send(text: str, token: str, chat_id: str) -> bool:
    try:
        r = requests.post(API.format(token=token), timeout=20, json={
            "chat_id": chat_id, "text": text,
            "parse_mode": "Markdown", "disable_web_page_preview": True})
    except Exception as e:
        print(f"전송 실패: {type(e).__name__}: {e}")
        return False
    if r.status_code != 200:
        # 토큰/chat_id 문제는 여기서 드러난다
        print(f"전송 실패 HTTP {r.status_code}: {r.text[:200]}")
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="전송하지 않고 출력만")
    ap.add_argument("--test", action="store_true", help="연결 확인 메시지 1건 전송")
    ap.add_argument("--reset", action="store_true", help="상태 초기화(다음 실행에 전부 재전송)")
    a = ap.parse_args()

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    dry = a.dry_run or not (token and chat_id)

    if a.reset:
        write_state(set())
        print("상태 초기화 완료")
        return 0

    if a.test:
        if dry:
            print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 가 없습니다.")
            print("scripts/set_telegram.py 로 먼저 등록하세요.")
            return 1
        ok = send("✅ 연결 확인 — 주식 대시보드 알림이 정상 동작합니다.", token, chat_id)
        print("전송 성공" if ok else "전송 실패")
        return 0 if ok else 1

    alerts = evaluate()
    active = {al.key for al in alerts}
    previous = read_state()
    fresh = [al for al in alerts if al.key not in previous]

    print(f"현재 참인 상태 {len(active)}건, 직전 대비 신규 {len(fresh)}건")
    for al in alerts:
        mark = "NEW" if al.key in active - previous else "   "
        print(f"  [{mark}] {al.key} — {al.title}")

    if not fresh:
        write_state(active)
        return 0

    header = f"*시장 대시보드* · {pd.Timestamp.now(tz='Asia/Seoul'):%Y-%m-%d %H:%M}"
    text = header + "\n\n" + "\n\n".join(al.format() for al in fresh)

    if dry:
        print("\n--- dry-run (전송 안 함) ---")
        print(text)
    else:
        if not send(text, token, chat_id):
            return 1     # 상태를 갱신하지 않는다 → 다음 실행에 재시도
        print(f"\n{len(fresh)}건 전송 완료")

    write_state(active)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
