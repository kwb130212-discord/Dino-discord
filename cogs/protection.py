import asyncio
import json
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import discord
from discord import app_commands
from discord.ext import commands

SETTINGS_FILE = "data/settings.json"
JOIN_THRESHOLD = 5
JOIN_WINDOW = 10
SPAM_THRESHOLD = 5
SPAM_WINDOW = 5
WARN_MUTE_THRESHOLD = 3
ALERT_CHANNEL_NAME = "보안-알림"
recent_joins = defaultdict(list)
message_count = defaultdict(list)
warnings = defaultdict(lambda: defaultdict(int))


def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"설정 파일 로드 실패: {exc}")
    return {}


def save_settings(data):
    os.makedirs("data", exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


class Protection(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.settings = load_settings()
        self.raid_mode_guilds = set()
        self.raid_tasks = {}

    def get_alert_ch(self, guild):
        return discord.utils.get(guild.channels, name=ALERT_CHANNEL_NAME)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        now = datetime.now(timezone.utc)
        guild_id = member.guild.id
        if guild_id in self.raid_mode_guilds:
            try:
                await member.kick(reason="레이드 모드 활성화 중")
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"레이드 모드 자동 킥 실패 ({guild_id}/{member.id}): {exc}")
            return

        joins = recent_joins[guild_id]
        joins[:] = [(t, m) for t, m in joins if (now - t).total_seconds() < JOIN_WINDOW]
        joins.append((now, member))
        if len(joins) >= JOIN_THRESHOLD:
            await self.activate_raid_mode(member.guild, joins)

    async def activate_raid_mode(self, guild, joiners):
        if guild.id in self.raid_mode_guilds:
            return
        self.raid_mode_guilds.add(guild.id)
        alert_ch = self.get_alert_ch(guild)
        kicked = []
        for _, member in joiners[-JOIN_THRESHOLD:]:
            try:
                await member.kick(reason="레이드 보호 자동 킥")
                kicked.append(str(member))
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"레이드 보호 자동 킥 실패 ({guild.id}/{member.id}): {exc}")
        embed = discord.Embed(
            title="🚨 레이드 감지! 보호 모드 활성화",
            description=(
                f"{JOIN_WINDOW}초 내 {len(joiners)}명 입장 감지\n"
                f"**자동 킥된 계정:** {', '.join(kicked) or '없음'}\n\n"
                "**레이드 모드가 활성화됐어요.**\n"
                "신규 입장자는 자동으로 킥됩니다.\n"
                "`/레이드모드해제` 로 해제하세요."
            ),
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc),
        )
        if alert_ch:
            await alert_ch.send(content="@everyone", embed=embed)
        old_task = self.raid_tasks.pop(guild.id, None)
        if old_task:
            old_task.cancel()
        self.raid_tasks[guild.id] = asyncio.create_task(self.raid_mode_watcher(guild))

    async def raid_mode_watcher(self, guild):
        try:
            await asyncio.sleep(300)
        except asyncio.CancelledError:
            return
        self.raid_mode_guilds.discard(guild.id)
        self.raid_tasks.pop(guild.id, None)
        alert_ch = self.get_alert_ch(guild)
        if alert_ch:
            await alert_ch.send("✅ 레이드 모드가 자동 해제됐어요. (5분 경과)")

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild or message.author.guild_permissions.manage_messages:
            return
        user_key = (message.guild.id, message.author.id)
        now = datetime.now(timezone.utc)
        times = message_count[user_key]
        times[:] = [t for t in times if (now - t).total_seconds() < SPAM_WINDOW]
        times.append(now)
        if len(times) >= SPAM_THRESHOLD:
            times.clear()
            await self.warn_user(message.guild, message.author, "스팸 감지", message.channel)

    async def warn_user(self, guild, member, reason, channel=None):
        guild_id, user_id = str(guild.id), str(member.id)
        warnings[guild_id][user_id] += 1
        count = warnings[guild_id][user_id]
        alert_ch = self.get_alert_ch(guild) or channel
        embed = discord.Embed(
            title=f"⚠️ 경고 ({count}/{WARN_MUTE_THRESHOLD})",
            description=f"{member.mention} | 사유: {reason}",
            color=discord.Color.yellow(),
            timestamp=datetime.now(timezone.utc),
        )
        if count >= WARN_MUTE_THRESHOLD:
            try:
                await member.timeout(timedelta(minutes=10), reason=f"경고 {count}회 누적: {reason}")
                embed.title = f"🔇 자동 뮤트 (경고 {count}회)"
                embed.color = discord.Color.red()
                warnings[guild_id][user_id] = 0
            except (discord.Forbidden, discord.HTTPException) as exc:
                embed.add_field(name="뮤트 실패", value=str(exc)[:1024])
        if alert_ch:
            await alert_ch.send(embed=embed)

    @app_commands.command(name="경고", description="멤버에게 경고를 부여합니다.")
    @app_commands.describe(멤버="경고할 멤버", 사유="경고 사유")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def warn(self, interaction, 멤버: discord.Member, 사유: str = "규칙 위반"):
        await self.warn_user(interaction.guild, 멤버, 사유, interaction.channel)
        await interaction.response.send_message(f"⚠️ {멤버.mention} 에게 경고를 부여했어요.", ephemeral=True)

    @app_commands.command(name="경고초기화", description="멤버의 경고를 초기화합니다.")
    @app_commands.checks.has_permissions(administrator=True)
    async def clear_warns(self, interaction, 멤버: discord.Member):
        warnings[str(interaction.guild.id)][str(멤버.id)] = 0
        await interaction.response.send_message(f"✅ {멤버.mention} 의 경고를 초기화했어요.", ephemeral=True)

    @app_commands.command(name="레이드모드해제", description="레이드 보호 모드를 수동으로 해제합니다.")
    @app_commands.checks.has_permissions(administrator=True)
    async def disable_raid(self, interaction):
        guild_id = interaction.guild.id
        self.raid_mode_guilds.discard(guild_id)
        task = self.raid_tasks.pop(guild_id, None)
        if task:
            task.cancel()
        await interaction.response.send_message("✅ 레이드 모드가 해제됐어요.", ephemeral=True)

    @app_commands.command(name="보안설정", description="보안 알림 채널을 생성합니다.")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_security(self, interaction):
        guild = interaction.guild
        if not discord.utils.get(guild.channels, name=ALERT_CHANNEL_NAME):
            await guild.create_text_channel(ALERT_CHANNEL_NAME)
        await interaction.response.send_message(f"✅ `{ALERT_CHANNEL_NAME}` 채널이 준비됐어요.", ephemeral=True)

    @app_commands.command(name="뮤트", description="멤버를 일시적으로 뮤트합니다.")
    @app_commands.describe(멤버="뮤트할 멤버", 분="뮤트 시간 (분)", 사유="뮤트 사유")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def mute(self, interaction, 멤버: discord.Member, 분: int = 10, 사유: str = "규칙 위반"):
        if not 1 <= 분 <= 40320:
            await interaction.response.send_message("뮤트 시간은 1~40320분 사이여야 해요.", ephemeral=True)
            return
        await 멤버.timeout(timedelta(minutes=분), reason=사유)
        await interaction.response.send_message(f"🔇 {멤버.mention} 을 {분}분간 뮤트했어요.", ephemeral=True)

    @app_commands.command(name="킥", description="멤버를 서버에서 킥합니다.")
    @app_commands.describe(멤버="킥할 멤버", 사유="킥 사유")
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(self, interaction, 멤버: discord.Member, 사유: str = "규칙 위반"):
        await 멤버.kick(reason=사유)
        await interaction.response.send_message(f"👢 {멤버.mention} 을 킥했어요.", ephemeral=True)

    @app_commands.command(name="밴", description="멤버를 영구 밴합니다.")
    @app_commands.describe(멤버="밴할 멤버", 사유="밴 사유")
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban(self, interaction, 멤버: discord.Member, 사유: str = "규칙 위반"):
        await 멤버.ban(reason=사유, delete_message_days=1)
        await interaction.response.send_message(f"🔨 {멤버.mention} 을 밴했어요.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Protection(bot))
