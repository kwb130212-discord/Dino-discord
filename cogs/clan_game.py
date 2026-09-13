import discord
from discord.ext import commands
from discord import app_commands
import random
import asyncio
import json
import os
from datetime import datetime, timezone

CLAN_DATA_FILE = "data/clan.json"

def load_clan():
    if os.path.exists(CLAN_DATA_FILE):
        with open(CLAN_DATA_FILE) as f:
            return json.load(f)
    return {}

def save_clan(data):
    os.makedirs("data", exist_ok=True)
    with open(CLAN_DATA_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── 내전 모집 버튼 뷰 ──────────────────────────────────
class InternView(discord.ui.View):
    def __init__(self, host: discord.Member, max_players: int, game_mode: str):
        super().__init__(timeout=1800)  # 30분
        self.host = host
        self.max_players = max_players
        self.game_mode = game_mode
        self.players: list[discord.Member] = [host]
        self.started = False
        self.message: discord.Message = None

    def make_embed(self):
        per_team = self.max_players // 2
        embed = discord.Embed(
            title=f"⚔️ 내전 모집 - {self.game_mode}",
            description=(
                f"**호스트:** {self.host.mention}\n"
                f"**모드:** {self.game_mode}\n"
                f"**정원:** {len(self.players)}/{self.max_players}명\n\n"
                f"**참가자:**\n" +
                "\n".join(f"• {p.display_name}" for p in self.players)
            ),
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text=f"팀당 {per_team}명 | 30분 내 마감")
        return embed

    @discord.ui.button(label="✋ 참가", style=discord.ButtonStyle.green, custom_id="join")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user in self.players:
            await interaction.response.send_message("이미 참가 중이에요!", ephemeral=True)
            return
        if len(self.players) >= self.max_players:
            await interaction.response.send_message("정원이 가득 찼어요!", ephemeral=True)
            return

        self.players.append(interaction.user)
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

        if len(self.players) >= self.max_players:
            await self.start_intern(interaction)

    @discord.ui.button(label="🚪 나가기", style=discord.ButtonStyle.red, custom_id="leave")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user == self.host:
            await interaction.response.send_message("호스트는 나갈 수 없어요. `/내전취소` 를 사용하세요.", ephemeral=True)
            return
        if interaction.user not in self.players:
            await interaction.response.send_message("참가하지 않은 상태예요.", ephemeral=True)
            return

        self.players.remove(interaction.user)
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.button(label="🎮 강제시작 (호스트)", style=discord.ButtonStyle.blurple, custom_id="force_start")
    async def force_start(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.host:
            await interaction.response.send_message("호스트만 강제시작할 수 있어요.", ephemeral=True)
            return
        if len(self.players) < 2:
            await interaction.response.send_message("최소 2명 이상 필요해요.", ephemeral=True)
            return
        await self.start_intern(interaction)

    async def start_intern(self, interaction: discord.Interaction):
        if self.started:
            return
        self.started = True
        self.stop()

        # 팀 랜덤 배정
        shuffled = self.players.copy()
        random.shuffle(shuffled)
        mid = len(shuffled) // 2
        team_a = shuffled[:mid]
        team_b = shuffled[mid:]

        embed = discord.Embed(
            title="⚔️ 내전 팀 배정 완료!",
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(
            name="🔵 팀 A",
            value="\n".join(f"• {m.mention}" for m in team_a),
            inline=True
        )
        embed.add_field(
            name="🔴 팀 B",
            value="\n".join(f"• {m.mention}" for m in team_b),
            inline=True
        )
        embed.set_footer(text=f"모드: {self.game_mode}")

        for btn in self.children:
            btn.disabled = True

        await interaction.response.edit_message(embed=embed, view=self)


# ── 팀짜기 뷰 ─────────────────────────────────────────
class TeamPickView(discord.ui.View):
    def __init__(self, players: list[discord.Member], team_count: int):
        super().__init__(timeout=60)
        self.players = players
        self.team_count = team_count

    @discord.ui.button(label="🔀 랜덤 팀 배정", style=discord.ButtonStyle.green)
    async def random_teams(self, interaction: discord.Interaction, button: discord.ui.Button):
        shuffled = self.players.copy()
        random.shuffle(shuffled)

        teams = [[] for _ in range(self.team_count)]
        for i, p in enumerate(shuffled):
            teams[i % self.team_count].append(p)

        embed = discord.Embed(title="🎮 팀 배정 결과", color=discord.Color.green())
        for i, team in enumerate(teams):
            embed.add_field(
                name=f"팀 {i+1}",
                value="\n".join(f"• {m.mention}" for m in team) or "없음",
                inline=True
            )
        self.stop()
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="🔀 다시 배정", style=discord.ButtonStyle.blurple)
    async def reshuffle(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.random_teams.callback(self, interaction, button)


class ClanGame(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.clan_data = load_clan()

    # ── 내전 모집 ──────────────────────────────────────
    @app_commands.command(name="내전", description="내전을 모집합니다.")
    @app_commands.describe(
        인원="총 인원 수 (짝수, 최대 20)",
        모드="게임 모드 (예: 5v5, 스크림, 팀데스매치)"
    )
    async def intern(self, interaction: discord.Interaction,
                     인원: int = 10,
                     모드: str = "5v5"):
        if 인원 % 2 != 0 or 인원 < 2 or 인원 > 20:
            await interaction.response.send_message("인원은 2~20 사이의 짝수여야 해요.", ephemeral=True)
            return

        view = InternView(interaction.user, 인원, 모드)
        await interaction.response.send_message(
            content="@here 내전 모집!",
            embed=view.make_embed(),
            view=view
        )
        view.message = await interaction.original_response()

    # ── 팀 짜기 ───────────────────────────────────────
    @app_commands.command(name="팀짜기", description="현재 음성 채널 인원을 랜덤으로 팀을 나눕니다.")
    @app_commands.describe(팀수="나눌 팀 수 (기본 2)")
    async def team_pick(self, interaction: discord.Interaction, 팀수: int = 2):
        if not interaction.user.voice:
            await interaction.response.send_message("먼저 음성 채널에 입장해주세요!", ephemeral=True)
            return

        vc = interaction.user.voice.channel
        members = [m for m in vc.members if not m.bot]

        if len(members) < 팀수:
            await interaction.response.send_message(f"팀 수({팀수})보다 인원이 부족해요. (현재 {len(members)}명)", ephemeral=True)
            return

        embed = discord.Embed(
            title="🎮 팀짜기",
            description=f"**{vc.name}** 의 {len(members)}명을 {팀수}팀으로 나눌게요.",
            color=discord.Color.blurple()
        )
        embed.add_field(name="참가자", value="\n".join(f"• {m.display_name}" for m in members))

        view = TeamPickView(members, 팀수)
        await interaction.response.send_message(embed=embed, view=view)

    # ── 클랜 정보 등록 ────────────────────────────────
    @app_commands.command(name="클랜등록", description="클랜 정보를 등록합니다.")
    @app_commands.describe(
        클랜명="클랜 이름",
        게임="주 게임 (예: 발로란트, 배그, 리그오브레전드)",
        소개="클랜 소개 (선택)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def register_clan(self, interaction: discord.Interaction,
                             클랜명: str,
                             게임: str,
                             소개: str = ""):
        guild_id = str(interaction.guild.id)
        self.clan_data[guild_id] = {
            "name": 클랜명,
            "game": 게임,
            "desc": 소개,
            "members": [],
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        save_clan(self.clan_data)
        await interaction.response.send_message(
            f"✅ **{클랜명}** 클랜이 등록됐어요!\n게임: {게임}", ephemeral=True
        )

    @app_commands.command(name="클랜정보", description="클랜 정보를 봅니다.")
    async def clan_info(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)
        data = self.clan_data.get(guild_id)

        if not data:
            await interaction.response.send_message("등록된 클랜 정보가 없어요. `/클랜등록` 을 사용하세요.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"🏆 {data['name']}",
            description=data.get("desc", "소개 없음"),
            color=discord.Color.gold()
        )
        embed.add_field(name="🎮 주 게임", value=data["game"], inline=True)
        embed.add_field(name="👥 멤버 수", value=f"{interaction.guild.member_count}명", inline=True)
        embed.set_thumbnail(url=interaction.guild.icon.url if interaction.guild.icon else None)
        await interaction.response.send_message(embed=embed)

    # ── 주사위 / 사다리타기 ───────────────────────────
    @app_commands.command(name="주사위", description="주사위를 굴립니다.")
    @app_commands.describe(면수="주사위 면 수 (기본 6)")
    async def roll_dice(self, interaction: discord.Interaction, 면수: int = 6):
        result = random.randint(1, 면수)
        await interaction.response.send_message(
            f"🎲 **{interaction.user.display_name}** 님이 {면수}면 주사위를 굴렸어요!\n결과: **{result}**"
        )

    @app_commands.command(name="사다리", description="멘션된 멤버들 중 한 명을 랜덤으로 뽑습니다.")
    @app_commands.describe(대상들="띄어쓰기로 구분해서 멘션 (예: @user1 @user2)")
    async def ladder(self, interaction: discord.Interaction, 대상들: str):
        mentions = interaction.message.mentions if hasattr(interaction, 'message') else []
        # 슬래시에서는 멘션 파싱이 다름 - 직접 처리
        import re
        ids = re.findall(r'<@!?(\d+)>', 대상들)
        members = []
        for uid in ids:
            m = interaction.guild.get_member(int(uid))
            if m:
                members.append(m)

        if not members:
            # 음성 채널 인원으로 대체
            if interaction.user.voice:
                members = [m for m in interaction.user.voice.channel.members if not m.bot]
            else:
                await interaction.response.send_message("멤버를 멘션하거나 음성 채널에 입장해주세요.", ephemeral=True)
                return

        winner = random.choice(members)
        await interaction.response.send_message(
            f"🎰 **사다리 결과:**\n\n🏆 당첨자: **{winner.mention}** ({winner.display_name})"
        )

    # ── 공지 도우미 ───────────────────────────────────
    @app_commands.command(name="내전공지", description="내전 일정을 공지합니다.")
    @app_commands.describe(
        날짜="날짜 (예: 오늘 저녁 9시)",
        모드="게임 모드",
        추가내용="추가 내용 (선택)"
    )
    async def intern_announce(self, interaction: discord.Interaction,
                               날짜: str,
                               모드: str = "5v5",
                               추가내용: str = ""):
        embed = discord.Embed(
            title="⚔️ 내전 일정 공지",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="📅 일정", value=날짜, inline=True)
        embed.add_field(name="🎮 모드", value=모드, inline=True)
        if 추가내용:
            embed.add_field(name="📋 추가 내용", value=추가내용, inline=False)
        embed.set_footer(text=f"주최: {interaction.user.display_name}")

        await interaction.response.send_message(content="@here", embed=embed)


async def setup(bot):
    await bot.add_cog(ClanGame(bot))
