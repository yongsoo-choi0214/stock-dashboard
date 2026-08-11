"""텔레그램 봇 토큰/채팅ID 를 .env 에 안전하게 기록한다.

**회원님 터미널에서 직접 실행하세요.** 토큰은 화면에 표시되지 않습니다.

    .venv/Scripts/python.exe scripts/set_telegram.py

--- 발급 순서 ---
1) 텔레그램에서 @BotFather 를 찾아 대화 시작
2) /newbot 입력 → 봇 이름과 사용자명(끝이 bot 이어야 함) 지정
3) `123456789:AAF...` 형태의 **토큰**을 받는다
4) 방금 만든 봇을 검색해 대화방을 열고 아무 메시지나 하나 보낸다
   (봇은 먼저 말을 걸 수 없다 — 이 단계를 빠뜨리면 전송이 실패한다)
5) 이 스크립트를 실행하면 chat_id 를 자동으로 찾아준다
"""
from __future__ import annotations

import argparse
import getpass
import shutil
import subprocess
import sys
from pathlib import Path

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"


def write_env(values: dict[str, str]) -> None:
    """관리 대상 키만 교체하고 나머지 줄은 보존한다."""
    lines = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    out, seen = [], set()
    for line in lines:
        name = line.split("=", 1)[0].strip() if "=" in line else ""
        if name in values:
            out.append(f"{name}={values[name]}")
            seen.add(name)
        else:
            out.append(line)
    for k, v in values.items():
        if k not in seen:
            out.append(f"{k}={v}")
    ENV.write_text("\n".join(out) + "\n", encoding="utf-8")


def find_chat_id(token: str) -> str | None:
    """getUpdates 로 최근 대화의 chat_id 를 찾는다."""
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20)
        js = r.json()
    except Exception as e:
        print(f"  조회 실패: {type(e).__name__}: {e}")
        return None

    if not js.get("ok"):
        print(f"  텔레그램 응답: {js.get('description', js)}")
        return None

    chats = {}
    for upd in js.get("result", []):
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            name = chat.get("username") or chat.get("title") or chat.get("first_name", "")
            chats[str(chat["id"])] = f"{name} ({chat.get('type')})"

    if not chats:
        print("  최근 메시지가 없습니다. 봇에게 아무 메시지나 보낸 뒤 다시 실행하세요.")
        return None
    if len(chats) == 1:
        cid, desc = next(iter(chats.items()))
        print(f"  찾음: {cid} — {desc}")
        return cid

    print("  여러 대화가 있습니다:")
    for i, (cid, desc) in enumerate(chats.items(), 1):
        print(f"    {i}) {cid} — {desc}")
    pick = input("  번호 선택: ").strip()
    try:
        return list(chats)[int(pick) - 1]
    except (ValueError, IndexError):
        return None


def existing(name: str) -> str:
    """이미 .env 에 있는 값. 토큰만 새로 받는 경우 chat_id 를 재사용한다."""
    if not ENV.exists():
        return ""
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    return ""


def push_secrets(token: str, chat_id: str, repo: str) -> bool:
    """GitHub Secrets 갱신. 값은 stdin 으로 넘겨 명령행·히스토리에 남기지 않는다."""
    if not shutil.which("gh"):
        print("  gh 명령을 찾을 수 없습니다 — 수동 등록이 필요합니다.")
        return False
    ok = True
    for name, value in [("TELEGRAM_BOT_TOKEN", token),
                        ("TELEGRAM_CHAT_ID", chat_id)]:
        r = subprocess.run(["gh", "secret", "set", name, "--repo", repo],
                           input=value, text=True, capture_output=True)
        if r.returncode == 0:
            print(f"  {name} 등록 완료")
        else:
            ok = False
            print(f"  {name} 실패: {r.stderr.strip()[:120]}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="yongsoo-choi0214/stock-dashboard")
    ap.add_argument("--no-secrets", action="store_true",
                    help="GitHub Secrets 갱신을 건너뛴다")
    a = ap.parse_args()

    print(f"\n대상 파일: {ENV}  (.gitignore 등록됨)\n")
    print("@BotFather 에서 받은 토큰을 붙여넣고 Enter.")
    print("입력해도 화면에 아무것도 안 보이는 게 정상입니다.\n")
    try:
        token = getpass.getpass("봇 토큰: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n취소했습니다.")
        return 1
    if ":" not in token:
        print("토큰 형식이 아닙니다 (예: 123456789:AAF...). 중단합니다.")
        return 1

    print("\nchat_id 확인 중…")
    chat_id = find_chat_id(token)
    if not chat_id:
        # 토큰만 재발급한 경우 대화방은 그대로다 — 기존 값을 쓴다
        chat_id = existing("TELEGRAM_CHAT_ID")
        if chat_id:
            print(f"  최근 메시지가 없어 기존 chat_id 를 재사용합니다: {chat_id}")
    if not chat_id:
        chat_id = input("  chat_id 직접 입력: ").strip()
    if not chat_id:
        print("chat_id 가 없으면 전송할 수 없습니다. 봇에게 메시지를 보낸 뒤 다시 실행하세요.")
        return 1

    write_env({"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": chat_id})
    print(f"\n.env 저장 완료 (토큰 {len(token)}자, chat_id {chat_id})")

    if not a.no_secrets:
        print(f"\nGitHub Secrets 갱신 ({a.repo})")
        push_secrets(token, chat_id, a.repo)

    print("\n연결 확인 중…")
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20,
                      json={"chat_id": chat_id,
                            "text": "✅ 새 토큰으로 연결됐습니다."})
    print("  전송 성공 — 휴대폰을 확인하세요." if r.status_code == 200
          else f"  전송 실패 {r.status_code}: {r.text[:150]}")
    return 0 if r.status_code == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
