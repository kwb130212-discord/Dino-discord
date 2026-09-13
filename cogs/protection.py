import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import asyncio
import json
import os

SETTINGS_FILE = "data/settings.json"

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    return {}

def save_settings(data):
    os.makedirs("data", exist_ok=True)
    with open(SETTINGS_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# 레이드 감지: 짧은 시간 내 다수 입장
JOIN_THRESHOLD = 5       # N명 이상 입장 시 레이드 판정
JOIN_WINDOW = 10         # 감지 윈도우 (초)
recent_joins: list = []  # (timestamp, member) 리스트

# 스팸 감지
message_count: defaultdict = defaultdict(list)  # user_id -> [timestamps]
SPAM_THRESHOLD = 5       # N개 이상 메시지
SPAM_WINDOW = 5          # 감지 윈도우 (초)

# 경고 시스템
warnings: defaultdict = defaultdict(lambda: defaultdict(int))  # guild_id -> user_id -> count
WARN_MUTE_THRESHOLD = 3  # 경고 N회 시 자동 뮤트 (timeout)

ALERT_CHANNEL_NAME = "보안-알림"


class Protection(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.settings = load_settings()
        self.raid_mode_guilds: set = set()   # 레이드 모드 활성화된 길드

    def get_alert_ch(self, guild):
        return discord.utils.get(guild.channels, name=ALERT_CHANNEL_NAME)

    # ── 레이드 보호 ────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        now = datetime.now(timezone.utc)
        global recent_joins

        # 윈도우 밖 기록 제거
        recent_joins = [(t, m) for t, m in recent_joins
                        if (now - t).total_seconds() < JOIN_WINDOW and m.guild.id == member.guild.id]
        recent_joins.append((now, member))

        guild_joins = [x for x in recent_joins if x[1].guild.id == member.guild.id]

        if len(guild_joins) >= JOIN_THRESHOLD:
            await self.activate_raid_mode(member.guild, guild_joins)

    async def activate_raid_mode(self, guild: discord.Guild, joiners):
        if guild.id in self.raid_mode_guilds:
            return  # 이미 활성화됨

        self.raid_mode_guilds.add(guild.id)
        alert_ch = self.get_alert_ch(guild)

        # 최근 입장자 모두 kick
        kicked = []
        for _, m in joiners[-JOIN_THRESHOLD:]:
            try:
                await m.kick(reason="레이드 보호 자동 킥")
                kicked.append(str(m))
            except:
                pass

        embed = discord.Embed(
            title="🚨 레이드 감지! 보호 모드 활성화",
            description=(
                f"{JOIN_WINDOW}초 내 {len(joiners)}명 입장 감지\n"
                f"**자동 킥된 계정:** {', '.join(kicked) or '없음'}\n\n"
                f"**레이드 모드가 활성화됐어요.**\n"
                f"신규 입장자는 자동으로 킥됩니다.\n"
                f"`/레이드모드해제` 로 해제하세요."
            ),
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text="@everyone에게 알림")
        if alert_ch:
            await alert_ch.send(content="@everyone", embed=embed)

        # 레이드 모드 중 추가 입장자 처리
        asyncio.create_task(self.raid_mode_watcher(guild))

    async def raid_mode_watcher(self, guild):
        """레이드 모드 동안 입장하는 모든 유저 자동 킥"""
        await asyncio.sleep(300)  # 5분 후 자동 해제
        self.raid_mode_guilds.discard(guild.id)
        alert_ch = self.get_alert_ch(guild)
        if alert_ch:
            await alert_ch.send("✅ 레이드 모드가 자동 해제됐어요. (5분 경과)")

    @commands.Cog.listener()
    async def on_member_join_raid(self, member: discord.Member):
        """레이드 모드 활성화 시 입장자 킥"""
        if member.guild.id in self.raid_mode_guilds:
            try:
                await member.kick(reason="레이드 모드 활성화 중")
            except:
                pass

    # ── 스팸 감지 ──────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if message.author.guild_permissions.manage_messages:
            return

        user_id = message.author.id
        now = datetime.now(timezone.utc)

        # 윈도우 밖 기록 제거
        message_count[user_id] = [t for t in message_count[user_id]
                                   if (now - t).total_seconds() < SPAM_WINDOW]
        message_count[user_id].append(now)

        if len(message_count[user_id]) >= SPAM_THRESHOLD:
            message_count[user_id] = []
            await self.warn_user(message.guild, message.author, "스팸 감지", message.channel)

    # ── 경고 시스템 ────────────────────────────────────
    async def warn_user(self, guild, member, reason, channel=None):
        guild_id = str(guild.id)
        user_id = str(member.id)
        warnings[guild_id][user_id] += 1
        count = warnings[guild_id][user_id]

        alert_ch = self.get_alert_ch(guild) or channel
        embed = discord.Embed(
            title=f"⚠️ 경고 ({count}/{WARN_MUTE_THRESHOLD})",
            description=f"{member.mention} | 사유: {reason}",
            color=discord.Color.yellow(),
            timestamp=datetime.now(timezone.utc)
        )

        if count >= WARN_MUTE_THRESHOLD:
            # 자동 타임아웃 (10분)
            try:
                await member.timeout(timedelta(minutes=10), reason=f"경고 {count}회 누적: {reason}")
                embed.title = f"🔇 자동 뮤트 (경고 {count}회)"
                embed.color = discord.Color.red()
                warnings[guild_id][user_id] = 0  # 초기화
            except Exception as e:
                embed.add_field(name="뮤트 실패", value=str(e))

        if alert_ch:
            await alert_ch.send(embed=embed)

    # ── 슬래시 커맨드 ──────────────────────────────────
    @app_commands.command(name="경고", description="멤버에게 경고를 부여합니다.")
    @app_commands.describe(멤버="경고할 멤버", 사유="경고 사유")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def warn(self, interaction: discord.Interaction, 멤버: discord.Member, 사유: str = "규칙 위반"):
        await self.warn_user(interaction.guild, 멤버, 사유, interaction.channel)
        await interaction.response.send_message(f"⚠️ {멤버.mention} 에게 경고를 부여했어요.", ephemeral=True)

    @app_commands.command(name="경고초기화", description="멤버의 경고를 초기화합니다.")
    @app_commands.checks.has_permissions(administrator=True)
    async def clear_warns(self, interaction: discord.Interaction, 멤버: discord.Member):
        guild_id = str(interaction.guild.id)
        user_id = str(멤버.id)
        warnings[guild_id][user_id] = 0
        await interaction.response.send_message(f"✅ {멤버.mention} 의 경고를 초기화했어요.", ephemeral=True)

    @app_commands.command(name="레이드모드해제", description="레이드 보호 모드를 수동으로 해제합니다.")
    @app_commands.checks.has_permissions(administrator=True)
    async def disable_raid(self, interaction: discord.Interaction):
        self.raid_mode_guilds.discard(interaction.guild.id)
        await interaction.response.send_message("✅ 레이드 모드가 해제됐어요.", ephemeral=True)

    @app_commands.command(name="보안설정", description="보안 알림 채널을 생성합니다.")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_security(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not discord.utils.get(guild.channels, name=ALERT_CHANNEL_NAME):
            await guild.create_text_channel(ALERT_CHANNEL_NAME)
        await interaction.response.send_message(f"✅ `{ALERT_CHANNEL_NAME}` 채널이 준비됐어요.", ephemeral=True)

    @app_commands.command(name="뮤트", description="멤버를 일시적으로 뮤트합니다.")
    @app_commands.describe(멤버="뮤트할 멤버", 분="뮤트 시간 (분)", 사유="뮤트 사유")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def mute(self, interaction: discord.Interaction, 멤버: discord.Member, 분: int = 10, 사유: str = "규칙 위반"):
        await 멤버.timeout(timedelta(minutes=분), reason=사유)
        await interaction.response.send_message(f"🔇 {멤버.mention} 을 {분}분간 뮤트했어요.", ephemeral=True)

    @app_commands.command(name="킥", description="멤버를 서버에서 킥합니다.")
    @app_commands.describe(멤버="킥할 멤버", 사유="킥 사유")
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, 멤버: discord.Member, 사유: str = "규칙 위반"):
        await 멤버.kick(reason=사유)
        await interaction.response.send_message(f"👢 {멤버.mention} 을 킥했어요.", ephemeral=True)

    @app_commands.command(name="밴", description="멤버를 영구 밴합니다.")
    @app_commands.describe(멤버="밴할 멤버", 사유="밴 사유")
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(self, interaction: discord.Interaction, 멤버: discord.Member, 사유: str = "규칙 위반"):
        await 멤버.ban(reason=사유, delete_message_days=1)
        await interaction.response.send_message(f"🔨 {멤버.mention} 을 밴했어요.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Protection(bot))
