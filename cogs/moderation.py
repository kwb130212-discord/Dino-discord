import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="청소", description="채널 메시지를 대량 삭제합니다.")
    @app_commands.describe(개수="삭제할 메시지 수 (최대 100)")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def purge(self, interaction: discord.Interaction, 개수: int = 10):
        if not 1 <= 개수 <= 100:
            await interaction.response.send_message("삭제 개수는 1~100 사이여야 해요.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=개수)
        await interaction.followup.send(f"🗑️ {len(deleted)}개 메시지를 삭제했어요.", ephemeral=True)

    @app_commands.command(name="공지", description="임베드 공지를 작성합니다.")
    @app_commands.describe(제목="공지 제목", 내용="공지 내용", 채널="공지할 채널 (기본: 현재 채널)", 색상="red/green/blue/gold/blurple")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def announce(self, interaction: discord.Interaction, 제목: str, 내용: str, 채널: discord.TextChannel = None, 색상: str = "blurple"):
        colors = {"red": discord.Color.red(), "green": discord.Color.green(), "blue": discord.Color.blue(), "gold": discord.Color.gold(), "blurple": discord.Color.blurple()}
        target = 채널 or interaction.channel
        embed = discord.Embed(title=f"📢 {제목}", description=내용, color=colors.get(색상.lower(), discord.Color.blurple()), timestamp=datetime.now(timezone.utc))
        embed.set_footer(text=f"공지: {interaction.user.display_name}")
        await target.send(embed=embed)
        await interaction.response.send_message(f"✅ {target.mention} 에 공지했어요.", ephemeral=True)

    @app_commands.command(name="서버정보", description="서버 정보를 표시합니다.")
    async def server_info(self, interaction: discord.Interaction):
        guild = interaction.guild
        embed = discord.Embed(title=f"🏠 {guild.name}", color=discord.Color.blurple(), timestamp=datetime.now(timezone.utc))
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="👑 서버장", value=guild.owner.mention if guild.owner else "알 수 없음", inline=True)
        embed.add_field(name="👥 멤버 수", value=f"{guild.member_count}명", inline=True)
        embed.add_field(name="💬 채널 수", value=f"{len(guild.channels)}개", inline=True)
        embed.add_field(name="🎭 역할 수", value=f"{len(guild.roles)}개", inline=True)
        embed.add_field(name="😀 이모지 수", value=f"{len(guild.emojis)}개", inline=True)
        embed.add_field(name="📅 생성일", value=guild.created_at.strftime("%Y년 %m월 %d일"), inline=True)
        embed.add_field(name="🆔 서버 ID", value=f"`{guild.id}`", inline=False)
        if guild.premium_subscription_count:
            embed.add_field(name="💎 부스트", value=f"{guild.premium_subscription_count}개 (Tier {guild.premium_tier})", inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="유저정보", description="유저 정보를 표시합니다.")
    @app_commands.describe(멤버="정보를 볼 멤버 (기본: 본인)")
    async def user_info(self, interaction: discord.Interaction, 멤버: discord.Member = None):
        target = 멤버 or interaction.user
        embed = discord.Embed(title=f"👤 {target.display_name}", color=target.color, timestamp=datetime.now(timezone.utc))
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="유저명", value=str(target), inline=True)
        embed.add_field(name="🆔 ID", value=f"`{target.id}`", inline=True)
        embed.add_field(name="봇 여부", value="✅" if target.bot else "❌", inline=True)
        embed.add_field(name="계정 생성", value=target.created_at.strftime("%Y-%m-%d"), inline=True)
        embed.add_field(name="서버 참가", value=target.joined_at.strftime("%Y-%m-%d") if target.joined_at else "알 수 없음", inline=True)
        roles = [r.mention for r in target.roles[1:]]
        embed.add_field(name=f"역할 ({len(roles)}개)", value=" ".join(roles[:10]) or "없음", inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="도움말", description="봇 명령어 목록을 봅니다.")
    async def help_cmd(self, interaction: discord.Interaction):
        sections = {
            "🔐 인증": ["/인증설정"], "📋 로그": ["/로그설정", "/자동밴설정"],
            "🛡️ 보안": ["/보안설정", "/경고", "/경고초기화", "/레이드모드해제", "/뮤트", "/킥", "/밴"],
            "📺 유튜브": ["/유튜브구독", "/유튜브목록", "/유튜브삭제"],
            "⚔️ 게임/클랜": ["/내전", "/팀짜기", "/클랜등록", "/클랜정보", "/내전공지", "/주사위", "/사다리"],
            "🔧 관리": ["/청소", "/공지", "/서버정보", "/유저정보"],
        }
        embed = discord.Embed(title="📖 클랜봇 명령어 목록", color=discord.Color.blurple())
        for section, cmds in sections.items():
            embed.add_field(name=section, value="\n".join(f"`{c}`" for c in cmds), inline=True)
        embed.set_footer(text="모든 명령어는 슬래시(/) 커맨드입니다.")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Moderation(bot))
