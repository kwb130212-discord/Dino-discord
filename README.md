# Dino-discord

Discord 서버 로거와 OAuth2 기반 사용자 복구 기능을 제공하는 봇입니다.

## 현재 기능

- `/register` — 서버 소유자 또는 관리자만 서버 등록 가능
- `/logchannel` — 삭제/수정 메시지 로그 채널 지정
- `/recoverykey` — `영구 복구키` 또는 `일회용 복구키` 생성
- `/recoverykeys` — 현재 서버 복구키 상태 확인
- GitHub Pages 프런트엔드에서 복구키 입력 → 역할 선택 → Discord OAuth2 인증
- `guilds.join`으로 **OAuth2로 직접 인증한 사용자 본인**을 지정 서버에 복구
- 선택한 역할을 복구 과정에서 지급
- 일회용 키는 복구 성공 후 자동 폐기
- 영구 키는 폐기 전까지 재사용 가능
- 복구키 원문은 DB에 저장하지 않고 SHA-256 해시만 저장
- 메시지 삭제/수정 로그
- `/userlookup` — 관리자 전용 공개 Discord 프로필 조회(ID, 계정 생성일, 서버 가입일, 봇 여부)
- SQLite 기반 서버/복구 설정 저장

## OAuth2 복구 흐름

1. 관리자가 `/recoverykey`로 영구 또는 일회용 키를 생성합니다.
2. GitHub Pages 웹사이트에서 사용자가 복구키를 입력합니다.
3. 웹사이트가 백엔드의 `/api/recovery/roles`를 호출해 지급 가능한 역할 목록을 가져옵니다.
4. 사용자가 역할을 선택하면 `/api/recovery/start`가 일회성 OAuth2 `state`를 만들고 Discord 인증 URL을 반환합니다.
5. 사용자는 Discord에서 `identify guilds.join` 권한을 직접 승인합니다.
6. 백엔드가 callback에서 `state`를 검증하고 authorization code를 서버 측에서 access token으로 교환합니다.
7. 백엔드는 인증된 Discord 사용자 본인에게만 `guilds.join`을 적용하고 선택 역할을 지급합니다.
8. 일회용 키는 성공한 복구 직후 폐기됩니다.

Discord의 `guilds.join`은 사용자의 OAuth2 access token과 해당 애플리케이션의 봇 권한을 요구합니다. 봇은 대상 서버에 이미 들어가 있어야 하며, Discord 문서상 `CREATE_INSTANT_INVITE` 권한이 필요합니다. 역할 지급에는 봇의 `MANAGE_ROLES` 권한과 역할 계층 조건도 필요합니다.

## GitHub Pages 연동

GitHub Pages는 HTML/CSS/JavaScript 같은 정적 파일을 호스팅합니다. 서버 측 OAuth2 client secret은 GitHub Pages에 넣으면 안 됩니다. 따라서 구조는 다음과 같습니다.

```text
사용자 브라우저
   │
   ▼
GitHub Pages (HTML/CSS/JS)
   │  POST /api/recovery/roles
   │  POST /api/recovery/start
   ▼
OAuth2 백엔드 (Dino-discord의 oauth_server.py)
   │
   ├── Discord OAuth2
   └── guilds.join + 역할 지급
```

`WEB_ORIGIN`에는 실제 GitHub Pages 주소를 넣고, `PUBLIC_BACKEND_URL`에는 봇과 `oauth_server.py`가 실행되는 공개 HTTPS 백엔드 주소를 넣습니다. GitHub Pages 자체는 Python/서버 사이드 코드를 실행하지 않으므로 백엔드는 별도 실행 환경이 필요합니다.

## 실행

```bash
python -m pip install -r requirements.txt
export DISCORD_TOKEN="YOUR_BOT_TOKEN"
export DISCORD_CLIENT_ID="YOUR_CLIENT_ID"
export DISCORD_CLIENT_SECRET="YOUR_CLIENT_SECRET"
export DISCORD_REDIRECT_URI="https://your-backend.example.com/oauth/callback"
export PUBLIC_BACKEND_URL="https://your-backend.example.com"
export WEB_ORIGIN="https://yourname.github.io"
python bot.py
```

Windows PowerShell:

```powershell
python -m pip install -r requirements.txt
$env:DISCORD_TOKEN="YOUR_BOT_TOKEN"
$env:DISCORD_CLIENT_ID="YOUR_CLIENT_ID"
$env:DISCORD_CLIENT_SECRET="YOUR_CLIENT_SECRET"
$env:DISCORD_REDIRECT_URI="https://your-backend.example.com/oauth/callback"
$env:PUBLIC_BACKEND_URL="https://your-backend.example.com"
$env:WEB_ORIGIN="https://yourname.github.io"
python bot.py
```

## Discord Developer Portal 설정

OAuth2 Redirects에 정확히 `DISCORD_REDIRECT_URI`를 등록해야 합니다. OAuth2 scope는 `identify`와 `guilds.join`을 사용합니다.

봇은 대상 서버에 이미 들어가 있어야 하고, 복구 과정에서 역할을 지급하려면 `Manage Roles`가 필요합니다. 역할은 봇의 최고 역할보다 아래에 있어야 합니다.

## 보안

- Discord 비밀번호를 받지 않습니다.
- 사용자 access token을 DB에 저장하지 않습니다.
- OAuth2 `state`를 검증해 CSRF를 방어합니다.
- client secret은 백엔드 환경변수에만 둡니다.
- IP, 이메일, 통신사, 운영체제, 비밀번호, 세션 토큰 등의 민감정보를 수집하거나 `/userlookup`으로 노출하지 않습니다.

## GitHub Pages

GitHub Pages는 저장소의 정적 파일을 웹사이트로 게시할 수 있습니다. 구매한 웹사이트 템플릿을 나중에 연동할 경우, 템플릿의 로그인/복구 버튼에서 위 백엔드 API를 호출하도록 JavaScript만 연결하면 됩니다.
