import json
import os
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

LOG_CHANNEL_NAME = "입퇴장-로그"
SETTINGS_FILE = "data/settings.json"


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


class Logs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.settings = load_settings()

    def get_log_channel(self, guild):
        return discord.utils.get(guild.channels, name=LOG_CHANNEL_NAME)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        ch = self.get_log_channel(member.guild)
        if not ch:
            return
        created_days = (datetime.now(timezone.utc) - member.created_at).days
        embed = discord.Embed(title="📥 멤버 입장", color=discord.Color.green(), timestamp=datetime.now(timezone.utc))
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="유저", value=f"{member.mention}\n`{member.id}`", inline=True)
        embed.add_field(name="계정 생성", value=f"{member.created_at.strftime('%Y-%m-%d')}\n({created_days}일 전)", inline=True)
        embed.add_field(name="현재 멤버 수", value=f"{member.guild.member_count}명", inline=True)
        if created_days < 30:
            embed.add_field(name="⚠️ 경고", value="신규 계정 (30일 미만)", inline=False)
        embed.set_footer(text=member.guild.name)
        await ch.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        ch = self.get_log_channel(member.guild)
        guild_id = str(member.guild.id)
        settings = self.settings.get(guild_id, {})
        auto_ban = settings.get("auto_ban", False)
        ban_reason = settings.get("ban_reason", "자동 밴: 서버 자진 퇴장")
        ban_applied = False
        if auto_ban:
            whitelist_role = settings.get("whitelist_role")
            role = discord.utils.get(member.guild.roles, name=whitelist_role) if whitelist_role else None
            if not role or role not in member.roles:
                try:
                    await member.guild.ban(member, reason=ban_reason, delete_message_days=0)
                    ban_applied = True
                except (discord.Forbidden, discord.HTTPException) as exc:
                    print(f"자동 밴 실패 ({member.id}): {exc}")
        if not ch:
            return
        embed = discord.Embed(
            title="📤 멤버 퇴장" + (" + 🔨 자동 밴 적용" if ban_applied else ""),
            color=discord.Color.red() if ban_applied else discord.Color.orange(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="유저", value=f"**{member.name}**\n`{member.id}`", inline=True)
        embed.add_field(name="서버 참가일", value=member.joined_at.strftime('%Y-%m-%d') if member.joined_at else "알 수 없음", inline=True)
        embed.add_field(name="현재 멤버 수", value=f"{member.guild.member_count}명", inline=True)
        roles = ", ".join(r.name for r in member.roles[1:]) or "없음"
        embed.add_field(name="보유 역할", value=roles[:500], inline=False)
        if ban_applied:
            embed.add_field(name="밴 사유", value=ban_reason, inline=False)
        await ch.send(embed=embed)

    @app_commands.command(name="자동밴설정", description="나간 멤버를 자동으로 밴합니다.")
    @app_commands.describe(활성화="자동 밴 활성화 여부", 화이트리스트역할="이 역할을 가진 멤버는 자동 밴에서 제외됩니다")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_auto_ban(self, interaction, 활성화: bool, 화이트리스트역할: discord.Role = None):
        guild_id = str(interaction.guild.id)
        self.settings.setdefault(guild_id, {})["auto_ban"] = 활성화
        if 화이트리스트역할:
            self.settings[guild_id]["whitelist_role"] = 화이트리스트역할.name
        save_settings(self.settings)
        status = "✅ 활성화" if 활성화 else "❌ 비활성화"
        msg = f"자동 밴이 **{status}** 됐어요."
        if 화이트리스트역할:
            msg += f"\n**{화이트리스트역할.name}** 역할은 제외됩니다."
        await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(name="로그설정", description="입퇴장 로그 채널을 생성합니다.")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_logs(self, interaction):
        guild = interaction.guild
        if not discord.utils.get(guild.channels, name=LOG_CHANNEL_NAME):
            await guild.create_text_channel(LOG_CHANNEL_NAME)
        await interaction.response.send_message(f"✅ `{LOG_CHANNEL_NAME}` 채널이 준비됐어요.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Logs(bot))
