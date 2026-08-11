"""app.py 스모크 테스트 (Phase 4 DoD).

핵심 검증 2가지
1. 위젯을 조작해도 예외 없이 재실행된다.
2. **네트워크가 끊겨도 정상 동작한다** = 앱이 외부 API를 호출하지 않는다(설계원칙 1).
   소켓을 막은 채 앱을 통째로 실행해 확인한다. 이 테스트가 깨지면
   뷰 레이어 어딘가에서 ETL을 호출하고 있다는 뜻이다.
"""
from __future__ import annotations

import socket
from pathlib import Path

import pytest

from src import store

APP = str(Path(__file__).resolve().parents[1] / "app.py")

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

TIMEOUT = 180


@pytest.fixture(scope="module")
def has_data() -> bool:
    if store.read("prices").empty and store.read("macro").empty:
        pytest.skip("data/*.parquet 비어 있음 — ETL 먼저 실행")
    return True


def _run() -> AppTest:
    return AppTest.from_file(APP, default_timeout=TIMEOUT).run()


def test_app_runs_without_exception(has_data):
    at = _run()
    assert not at.exception, [e.value for e in at.exception]


def test_renders_core_elements(has_data):
    at = _run()
    assert at.title[0].value.startswith("매크로 유동성")
    labels = [t.label for t in at.tabs]
    for expected in ("개요", "한국 시장", "유동성", "크로스에셋"):
        assert expected in labels, f"'{expected}' 탭 없음 (현재: {labels})"
    assert len(at.metric) >= 1
    assert not at.error


def test_meta_badge_present(has_data):
    """'데이터가 언제 갱신됐는지 모르는 대시보드는 신뢰할 수 없다' (§3.4)."""
    at = _run()
    assert any("최종 갱신" in c.value for c in at.caption)


@pytest.mark.parametrize("value", [7, 14, 28])
def test_rsi_slider(has_data, value):
    at = _run()
    at.slider(key="rsi_n").set_value(value).run()
    assert not at.exception, [e.value for e in at.exception]


def test_theme_toggle(has_data):
    at = _run()
    at.radio(key="theme").set_value("dark").run()
    assert not at.exception, [e.value for e in at.exception]
    assert len(at.metric) >= 1


@pytest.mark.parametrize("period", ["6개월", "10년", "전체"])
def test_period_change(has_data, period):
    at = _run()
    at.select_slider(key="period").set_value(period).run()
    assert not at.exception, [e.value for e in at.exception]


def test_macd_fast_ge_slow_is_handled(has_data):
    """fast >= slow 는 사용자 오입력이지 크래시 사유가 아니다."""
    at = _run()
    at.number_input(key="macd_fast").set_value(50).run()     # fast=50 > slow=26
    assert not at.exception, [e.value for e in at.exception]


def test_works_without_network(has_data, monkeypatch):
    """설계원칙 1: 앱은 data/*.parquet 만 읽는다."""

    def blocked(*a, **kw):
        raise AssertionError(
            "앱이 네트워크를 호출했다 — ETL과 뷰의 분리가 깨졌다 (설계원칙 1)")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)

    at = _run()
    assert not at.exception, [e.value for e in at.exception]
    assert len(at.metric) >= 1
