# 🎮 클랜 디스코드 봇

게임 클랜 서버를 위한 올인원 디스코드 봇

---

## ✨ 기능 목록

| 카테고리 | 기능 |
|---|---|
| 🔐 인증 | 4자리 캡챠 + 질문형 인증, 실패 시 자동 킥 |
| 📋 로그 | 멤버 입퇴장 로그, 자동 밴 |
| 🛡️ 보안 | 레이드 보호, 스팸 감지, 경고 시스템 |
| 📺 유튜브 | 새 영상 자동 알림 (RSS, API 키 불필요) |
| ⚔️ 내전 | 참가 버튼형 내전 모집, 자동 팀 배정 |
| 🎮 팀짜기 | 음성 채널 인원 랜덤 팀 배정 |
| 🏆 클랜 | 클랜 정보 등록/조회 |
| 🔧 관리 | 청소, 공지, 서버/유저 정보 |

---

## 🚀 설치 방법

### 1. Discord Developer Portal 설정
1. https://discord.com/developers/applications 접속
2. **New Application** 클릭
3. **Bot** 탭 → **Add Bot**
4. **Privileged Gateway Intents** 모두 활성화:
   - Server Members Intent ✅
   - Message Content Intent ✅
   - Presence Intent ✅
5. 토큰 복사 → `.env` 파일에 저장

### 2. 봇 서버 초대
OAuth2 → URL Generator에서:
- Scopes: `bot`, `applications.commands`
- Permissions: `Administrator` (또는 필요한 권한만)

### 3. 로컬 실행
```bash
pip install -r requirements.txt
cp .env.example .env
# .env 파일에 토큰 입력
python main.py
```

---

## ☁️ 무료 호스팅 (카드 불필요)

### 추천 1: **bot-hosting.net** ⭐
- 무료 1봇, 카드 없음, 24/7
- https://bot-hosting.net
- GitHub 연동 지원

### 추천 2: **HeavenCloud** ⭐
- 512MB RAM, 1GB SSD, 24/7, 카드 없음
- https://heavencloud.in
- Pterodactyl 패널

### 추천 3: **dishost.org**
- Python 지원, 무료, GitHub push 자동 배포
- https://dishost.org

### Dishost 배포 방법
1. https://dishost.org 가입
2. GitHub 레포 연결
3. Start Command: `python main.py`
4. Environment Variables에 `DISCORD_TOKEN` 추가
5. 배포 완료 → main 브랜치 push 시 자동 재배포

---

## 📋 봇 초기 설정 명령어

서버에 봇 추가 후 순서대로 실행:

```
/인증설정       - 인증 채널 및 역할 생성
/로그설정       - 입퇴장 로그 채널 생성
/보안설정       - 보안 알림 채널 생성
/클랜등록       - 클랜 정보 등록
```

---

## ⚙️ 설정 파일

`cogs/verification.py` 상단에서 변경 가능:
```python
VERIFIED_ROLE_NAME = "인증완료"     # 인증 후 역할 이름
UNVERIFIED_ROLE_NAME = "미인증"     # 입장 시 역할 이름
MAX_ATTEMPTS = 3                    # 최대 인증 시도 횟수
TIMEOUT_SECONDS = 120               # 인증 제한 시간
```

`cogs/protection.py` 상단에서 변경 가능:
```python
JOIN_THRESHOLD = 5      # 레이드 감지 인원 기준
JOIN_WINDOW = 10        # 레이드 감지 시간 (초)
SPAM_THRESHOLD = 5      # 스팸 감지 메시지 수
WARN_MUTE_THRESHOLD = 3 # 자동 뮤트 경고 횟수
```

---

## 📁 파일 구조

```
clan_bot/
├── main.py                  # 봇 메인
├── requirements.txt
├── .env                     # 토큰 (git에 올리지 마세요!)
├── .env.example
├── data/                    # 자동 생성 (설정 저장)
└── cogs/
    ├── verification.py      # 인증 시스템
    ├── logs.py              # 입퇴장 로그 + 자동밴
    ├── protection.py        # 레이드보호 + 경고
    ├── youtube.py           # 유튜브 알림
    ├── clan_game.py         # 내전, 팀짜기
    └── moderation.py        # 청소, 공지 등
```

---

> ⚠️ `.env` 파일은 절대 GitHub에 올리지 마세요. `.gitignore`에 추가하세요.
