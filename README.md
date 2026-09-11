# Dino-discord

개인정보를 수집하지 않는 Discord 로거봇 기본 구현입니다.

## 현재 기능

- `/등록` — 서버 소유자 또는 관리자만 서버 등록 가능
- `/로그채널` — 서버 소유자 또는 관리자만 로그 채널 지정 가능
- 메시지 삭제 로그
- 메시지 수정 로그
- `/유저조회` — 관리자 전용 공개 Discord 프로필 조회(ID, 계정 생성일, 서버 가입일, 봇 여부)
- SQLite 기반 서버 설정 저장

## 실행

```bash
python -m pip install -r requirements.txt
export DISCORD_TOKEN="YOUR_BOT_TOKEN"
python bot.py
```

Windows PowerShell:

```powershell
python -m pip install -r requirements.txt
$env:DISCORD_TOKEN="YOUR_BOT_TOKEN"
python bot.py
```

봇 초대 시 `bot` 및 `applications.commands` 권한을 포함하고, 메시지 내용 로그를 사용하려면 Discord Developer Portal에서 **Message Content Intent**를 활성화해야 합니다.

## 개인정보 보호 범위

IP 주소, 이메일, 통신사, 운영체제, 비밀번호, 인증 토큰 등 Discord가 봇에 제공하지 않는 민감정보를 수집하거나 `/유저조회`로 노출하도록 구현하지 않습니다.
