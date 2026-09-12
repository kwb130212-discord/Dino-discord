import json
import os
import random
import re
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

CLAN_DATA_FILE = "data/clan.json"


def load_clan():
    if not os.path.exists(CLAN_DATA_FILE):
        return {}
    try:
        with open(CLAN_DATA_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"클랜 데이터 로드 실패: {exc}")
        return {}


def save_clan(data):
    os.makedirs("data", exist_ok=True)
    with open(CLAN_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


class InternView(discord.ui.View):
    def __init__(self, host, max_players, game_mode):
        super().__init__(timeout=1800)
        self.host = host
        self.max_players = max_players
        self.game_mode = game_mode
        self.players = [host]
        self.started = False

    def make_embed(self):
        embed = discord.Embed(
            title=f"⚔️ 내전 모집 - {self.game_mode}",
            description=(f"**호스트:** {self.host.mention}\n**모드:** {self.game_mode}\n"
                         f"**정원:** {len(self.players)}/{self.max_players}명\n\n**참가자:**\n" +
                         "\n".join(f"• {p.display_name}" for p in self.players)),
            color=discord.Color.blurple(), timestamp=datetime.now(timezone.utc))
        embed.set_footer(text=f"팀당 {self.max_players // 2}명 | 30분 내 마감")
        return embed

    @discord.ui.button(label="✋ 참가", style=discord.ButtonStyle.green)
    async def join(self, interaction, button):
        if interaction.user in self.players:
            await interaction.response.send_message("이미 참가 중이에요!", ephemeral=True)
            return
        if len(self.players) >= self.max_players:
            await interaction.response.send_message("정원이 가득 찼어요!", ephemeral=True)
            return
        self.players.append(interaction.user)
        if len(self.players) >= self.max_players:
            await self.start_intern(interaction)
        else:
            await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.button(label="🚪 나가기", style=discord.ButtonStyle.red)
    async def leave(self, interaction, button):
        if interaction.user == self.host:
            await interaction.response.send_message("호스트는 나갈 수 없어요. 모집 메시지를 종료하려면 관리자에게 문의하세요.", ephemeral=True)
            return
        if interaction.user not in self.players:
            await interaction.response.send_message("참가하지 않은 상태예요.", ephemeral=True)
            return
        self.players.remove(interaction.user)
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.button(label="🎮 강제시작 (호스트)", style=discord.ButtonStyle.blurple)
    async def force_start(self, interaction, button):
        if interaction.user != self.host:
            await interaction.response.send_message("호스트만 강제시작할 수 있어요.", ephemeral=True)
            return
        if len(self.players) < 2:
            await interaction.response.send_message("최소 2명 이상 필요해요.", ephemeral=True)
            return
        await self.start_intern(interaction)

    async def start_intern(self, interaction):
        if self.started:
            return
        self.started = True
        self.stop()
        shuffled = self.players.copy()
        random.shuffle(shuffled)
        mid = len(shuffled) // 2
        team_a, team_b = shuffled[:mid], shuffled[mid:]
        embed = discord.Embed(title="⚔️ 내전 팀 배정 완료!", color=discord.Color.gold(), timestamp=datetime.now(timezone.utc))
        embed.add_field(name="🔵 팀 A", value="\n".join(f"• {m.mention}" for m in team_a) or "없음", inline=True)
        embed.add_field(name="🔴 팀 B", value="\n".join(f"• {m.mention}" for m in team_b) or "없음", inline=True)
        embed.set_footer(text=f"모드: {self.game_mode}")
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)


class TeamPickView(discord.ui.View):
    def __init__(self, players, team_count):
        super().__init__(timeout=60)
        self.players = players
        self.team_count = team_count

    async def build_result(self, interaction):
        shuffled = self.players.copy()
        random.shuffle(shuffled)
        teams = [[] for _ in range(self.team_count)]
        for i, player in enumerate(shuffled):
            teams[i % self.team_count].append(player)
        embed = discord.Embed(title="🎮 팀 배정 결과", color=discord.Color.green())
        for i, team in enumerate(teams):
            embed.add_field(name=f"팀 {i + 1}", value="\n".join(f"• {m.mention}" for m in team) or "없음", inline=True)
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()

    @discord.ui.button(label="🔀 랜덤 팀 배정", style=discord.ButtonStyle.green)
    async def random_teams(self, interaction, button):
        await self.build_result(interaction)

    @discord.ui.button(label="🔀 다시 배정", style=discord.ButtonStyle.blurple)
    async def reshuffle(self, interaction, button):
        await self.build_result(interaction)


class ClanGame(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.clan_data = load_clan()

    @app_commands.command(name="내전", description="내전을 모집합니다.")
    @app_commands.describe(인원="총 인원 수 (짝수, 최대 20)", 모드="게임 모드")
    async def intern(self, interaction, 인원: int = 10, 모드: str = "5v5"):
        if 인원 % 2 or not 2 <= 인원 <= 20:
            await interaction.response.send_message("인원은 2~20 사이의 짝수여야 해요.", ephemeral=True)
            return
        view = InternView(interaction.user, 인원, 모드)
        await interaction.response.send_message(content="@here 내전 모집!", embed=view.make_embed(), view=view)

    @app_commands.command(name="팀짜기", description="현재 음성 채널 인원을 랜덤으로 팀을 나눕니다.")
    @app_commands.describe(팀수="나눌 팀 수 (2~10)")
    async def team_pick(self, interaction, 팀수: int = 2):
        if not 2 <= 팀수 <= 10:
            await interaction.response.send_message("팀 수는 2~10 사이여야 해요.", ephemeral=True)
            return
        if not interaction.user.voice:
            await interaction.response.send_message("먼저 음성 채널에 입장해주세요!", ephemeral=True)
            return
        members = [m for m in interaction.user.voice.channel.members if not m.bot]
        if len(members) < 팀수:
            await interaction.response.send_message(f"팀 수({팀수})보다 인원이 부족해요. (현재 {len(members)}명)", ephemeral=True)
            return
        embed = discord.Embed(title="🎮 팀짜기", description=f"**{interaction.user.voice.channel.name}** 의 {len(members)}명을 {팀수}팀으로 나눌게요.", color=discord.Color.blurple())
        embed.add_field(name="참가자", value="\n".join(f"• {m.display_name}" for m in members))
        await interaction.response.send_message(embed=embed, view=TeamPickView(members, 팀수))

    @app_commands.command(name="클랜등록", description="클랜 정보를 등록합니다.")
    @app_commands.describe(클랜명="클랜 이름", 게임="주 게임", 소개="클랜 소개")
    @app_commands.checks.has_permissions(administrator=True)
    async def register_clan(self, interaction, 클랜명: str, 게임: str, 소개: str = ""):
        self.clan_data[str(interaction.guild.id)] = {"name": 클랜명, "game": 게임, "desc": 소개, "members": [], "created_at": datetime.now(timezone.utc).isoformat()}
        save_clan(self.clan_data)
        await interaction.response.send_message(f"✅ **{클랜명}** 클랜이 등록됐어요!\n게임: {게임}", ephemeral=True)

    @app_commands.command(name="클랜정보", description="클랜 정보를 봅니다.")
    async def clan_info(self, interaction):
        data = self.clan_data.get(str(interaction.guild.id))
        if not data:
            await interaction.response.send_message("등록된 클랜 정보가 없어요. `/클랜등록` 을 사용하세요.", ephemeral=True)
            return
        embed = discord.Embed(title=f"🏆 {data['name']}", description=data.get("desc", "소개 없음"), color=discord.Color.gold())
        embed.add_field(name="🎮 주 게임", value=data["game"], inline=True)
        embed.add_field(name="👥 멤버 수", value=f"{interaction.guild.member_count}명", inline=True)
        if interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="주사위", description="주사위를 굴립니다.")
    @app_commands.describe(면수="주사위 면 수 (2~1000)")
    async def roll_dice(self, interaction, 면수: int = 6):
        if not 2 <= 면수 <= 1000:
            await interaction.response.send_message("면수는 2~1000 사이여야 해요.", ephemeral=True)
            return
        await interaction.response.send_message(f"🎲 **{interaction.user.display_name}** 님이 {면수}면 주사위를 굴렸어요!\n결과: **{random.randint(1, 면수)}**")

    @app_commands.command(name="사다리", description="멘션된 멤버들 중 한 명을 랜덤으로 뽑습니다.")
    @app_commands.describe(대상들="띄어쓰기로 구분한 멘션")
    async def ladder(self, interaction, 대상들: str):
        ids = re.findall(r"<@!?(\d+)>", 대상들)
        members = [m for uid in ids if (m := interaction.guild.get_member(int(uid)))]
        if not members and interaction.user.voice:
            members = [m for m in interaction.user.voice.channel.members if not m.bot]
        if not members:
            await interaction.response.send_message("멤버를 멘션하거나 음성 채널에 입장해주세요.", ephemeral=True)
            return
        winner = random.choice(members)
        await interaction.response.send_message(f"🎰 **사다리 결과:**\n\n🏆 당첨자: **{winner.mention}** ({winner.display_name})")

    @app_commands.command(name="내전공지", description="내전 일정을 공지합니다.")
    @app_commands.describe(날짜="날짜/시간", 모드="게임 모드", 추가내용="추가 내용")
    async def intern_announce(self, interaction, 날짜: str, 모드: str = "5v5", 추가내용: str = ""):
        embed = discord.Embed(title="⚔️ 내전 일정 공지", color=discord.Color.blurple(), timestamp=datetime.now(timezone.utc))
        embed.add_field(name="📅 일정", value=날짜, inline=True)
        embed.add_field(name="🎮 모드", value=모드, inline=True)
        if 추가내용:
            embed.add_field(name="📋 추가 내용", value=추가내용, inline=False)
        embed.set_footer(text=f"주최: {interaction.user.display_name}")
        await interaction.response.send_message(content="@here", embed=embed)


async def setup(bot):
    await bot.add_cog(ClanGame(bot))
