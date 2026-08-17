import os
import asyncio
from datetime import datetime, timezone, timedelta
import discord
from discord.ext import commands
from discord import app_commands
from fastapi import FastAPI, Request, HTTPException
import uvicorn
import sqlite3
from contextlib import asynccontextmanager

# 한국 시간대 설정
KST = timezone(timedelta(hours=9))

# 환경 변수 로드
TOKEN = os.getenv("DISCORD_TOKEN")
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
ADMIN_ROLE_NAME = os.getenv("ADMIN_ROLE_NAME", "! !디노")

# ==============================================================================
# 1. 로컬 데이터베이스 (SQLite) 관리 클래스
# ==============================================================================
class DatabaseManager:
    def __init__(self, db_file="local_database.db"):
        self.db_file = db_file

    def get_connection(self):
        return sqlite3.connect(self.db_file)

    def init_db(self):
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS server_schedules (
                    schedule_type TEXT PRIMARY KEY,
                    title TEXT,
                    content TEXT,
                    color_code INTEGER,
                    footer_text TEXT
                )
            """)
            conn.commit()
            cur.close()
            conn.close()
            print("로컬 데이터베이스(SQLite) 초기화 완료!")
        except Exception as e:
            print(f"로컬 데이터베이스 초기화 에러: {e}")

DB = DatabaseManager()

# ==============================================================================
# 2. FastAPI 웹 서버 설정
# ==============================================================================
app = FastAPI()

@app.get("/")
async def root():
    return {"status": "online", "message": "Discord Bot & Web Server is running!"}

@app.get("/auth/callback")
async def auth_callback(code: str = None):
    if not code:
        raise HTTPException(status_code=400, detail="인증 코드가 없습니다.")
    return {"message": "디스코드 인증 및 연동이 완료되었습니다!"}


# ==============================================================================
# 3. 디스코드 봇 및 Cogs 정의
# ==============================================================================
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

class ScheduleEditModal(discord.ui.Modal):
    def __init__(self, schedule_type: str, schedule_name: str):
        super().__init__(title=f"🛠️ {schedule_name} 설정")
        self.schedule_type = schedule_type

        self.sched_title = discord.ui.TextInput(
            label="임베드 제목",
            placeholder="예: 📅 정기 훈련 일정 안내",
            max_length=100,
            required=True
        )
        self.sched_content = discord.ui.TextInput(
            label="내용 (줄바꿈 가능)",
            placeholder="상세 내용을 입력하세요...",
            style=discord.TextStyle.paragraph,
            required=True
        )
        self.sched_footer = discord.ui.TextInput(
            label="하단 푸터 텍스트 (선택사항)",
            placeholder="예: 훈련 시작 10분 전 입장",
            required=False,
            max_length=200
        )

        self.add_item(self.sched_title)
        self.add_item(self.sched_content)
        self.add_item(self.sched_footer)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            conn = DB.get_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO server_schedules (schedule_type, title, content, color_code, footer_text)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(schedule_type) 
                DO UPDATE SET title = excluded.title, content = excluded.content, footer_text = excluded.footer_text
            """, (self.schedule_type, self.sched_title.value, self.sched_content.value, discord.Color.blue().value, self.sched_footer.value or None))
            conn.commit()
            cur.close()
            conn.close()

            await interaction.response.send_message(f"✅ 성공적으로 **{self.title}** 내용이 업데이트되었습니다!", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ 저장 중 오류 발생: {e}", ephemeral=True)


class SchedulePanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="훈련일정 수정", style=discord.ButtonStyle.primary, custom_id="edit_training_schedule")
    async def edit_training(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ScheduleEditModal("training", "훈련 일정"))

    @discord.ui.button(label="내전일정 수정", style=discord.ButtonStyle.success, custom_id="edit_civil_schedule")
    async def edit_civil(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ScheduleEditModal("civil", "내전 일정"))

    @discord.ui.button(label="클전일정 수정", style=discord.ButtonStyle.danger, custom_id="edit_clan_schedule")
    async def edit_clan(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ScheduleEditModal("clan", "클랜전 일정"))

    @discord.ui.button(label="티어루틴 수정", style=discord.ButtonStyle.secondary, custom_id="edit_routine_schedule")
    async def edit_routine(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ScheduleEditModal("routine", "티어별 훈련 루틴"))


class ScheduleCog(commands.Cog):
    def __init__(self, bot): 
        self.bot = bot

    async def get_schedule_data(self, schedule_type: str):
        try:
            conn = DB.get_connection()
            cur = conn.cursor()
            cur.execute("SELECT title, content, color_code, footer_text FROM server_schedules WHERE schedule_type = ?", (schedule_type,))
            row = cur.fetchone()
            cur.close()
            conn.close()
            return row
        except Exception as e:
            print(f"일정 조회 에러: {e}")
            return None

    @app_commands.command(name="훈련일정", description="서버 정기 훈련 일정을 확인합니다.")
    async def training_schedule(self, interaction: discord.Interaction):
        data = await self.get_schedule_data("training")
        if not data:
            await interaction.response.send_message("등록된 훈련 일정이 없습니다. 관리자 패널로 등록해 주세요.", ephemeral=True)
            return
        title, content, color, footer = data
        embed = discord.Embed(title=title, description=content, color=color or discord.Color.blue(), timestamp=datetime.now(KST))
        if footer: embed.set_footer(text=footer)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="내전일정", description="주말 내전 및 이벤트 경기 일정을 확인합니다.")
    async def civil_schedule(self, interaction: discord.Interaction):
        data = await self.get_schedule_data("civil")
        if not data:
            await interaction.response.send_message("등록된 내전 일정이 없습니다. 관리자 패널로 등록해 주세요.", ephemeral=True)
            return
        title, content, color, footer = data
        embed = discord.Embed(title=title, description=content, color=color or discord.Color.gold(), timestamp=datetime.now(KST))
        if footer: embed.set_footer(text=footer)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="클전일정", description="클랜전 및 외부 대항전 일정을 확인합니다.")
    async def clan_war_schedule(self, interaction: discord.Interaction):
        data = await self.get_schedule_data("clan")
        if not data:
            await interaction.response.send_message("등록된 클랜전 일정이 없습니다. 관리자 패널로 등록해 주세요.", ephemeral=True)
            return
        title, content, color, footer = data
        embed = discord.Embed(title=title, description=content, color=color or discord.Color.red(), timestamp=datetime.now(KST))
        if footer: embed.set_footer(text=footer)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="티어별훈련루틴", description="실력 향상을 위한 티어별 개인 훈련 루틴을 확인합니다.")
    async def tier_routine(self, interaction: discord.Interaction):
        data = await self.get_schedule_data("routine")
        if not data:
            await interaction.response.send_message("등록된 티어별 훈련 루틴이 없습니다. 관리자 패널로 등록해 주세요.", ephemeral=True)
            return
        title, content, color, footer = data
        embed = discord.Embed(title=title, description=content, color=color or discord.Color.purple(), timestamp=datetime.now(KST))
        if footer: embed.set_footer(text=footer)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="일정패널", description="[관리자용] 버튼 인터페이스로 일정을 간편하게 관리할 수 있는 패널을 띄웁니다.")
    async def schedule_panel(self, interaction: discord.Interaction):
        if not any(role.name == ADMIN_ROLE_NAME for role in interaction.user.roles) and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("이 명령어를 사용할 권한이 없습니다.", ephemeral=True)
            return

        embed = discord.Embed(
            title="⚙️ 서버 일정 및 루틴 관리 패널",
            description="아래 버튼을 클릭하여 각 일정의 제목과 내용을 간편하게 수정할 수 있습니다.",
            color=discord.Color.dark_embed()
        )
        await interaction.response.send_message(embed=embed, view=SchedulePanelView(), ephemeral=True)

    @app_commands.command(name="강제동기화", description="[관리자용] 슬래시 명령어를 즉시 동기화합니다.")
    async def force_sync(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
            return
        await self.bot.tree.sync()
        await interaction.response.send_message("🔄 슬래시 명령어가 성공적으로 동기화되었습니다!", ephemeral=True)


# ==============================================================================
# 4. 봇 클래스 및 라이프스판 설정
# ==============================================================================
class DinoBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        DB.init_db()
        await self.add_cog(ScheduleCog(self))
        self.add_view(SchedulePanelView())
        print("모든 모듈 및 뷰가 로드되었습니다.")

    async def on_ready(self):
        print(f"로그인 완료: {self.user} (ID: {self.user.id})")
        try:
            synced = await self.tree.sync()
            print(f"슬래시 명령어 동기화 완료: {len(synced)}개")
        except Exception as e:
            print(f"동기화 에러: {e}")

bot = DinoBot()

@asynccontextmanager
async def lifespan(app: FastAPI):
    bot_task = asyncio.create_task(bot.start(TOKEN))
    yield
    await bot.close()
    await bot_task

app.router.lifespan_context = lifespan

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
