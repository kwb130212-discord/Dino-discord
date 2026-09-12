import asyncio
import json
import os
import random

import discord
from discord import app_commands
from discord.ext import commands

VERIFIED_ROLE_NAME = "인증완료"
UNVERIFIED_ROLE_NAME = "미인증"
VERIFY_CHANNEL_NAME = "인증"
MAX_ATTEMPTS = 3
TIMEOUT_SECONDS = 120

QUESTIONS = [
    {"q": "디스코드 클랜에 처음 가입한 봇의 이름 첫 글자는? (예: 클봇이면 '클')", "type": "text"},
    {"q": "1+1 은?", "a": "2"},
    {"q": "빨간색의 영어 단어 첫 글자는? (대문자)", "a": "R"},
    {"q": "5 × 3 의 결과는?", "a": "15"},
    {"q": "한국 수도 이름 첫 글자는?", "a": "서"},
    {"q": "7 - 4 는?", "a": "3"},
    {"q": "디스코드 앱 아이콘 색깔은? (영어 소문자)", "a": "blurple"},
    {"q": "'안녕하세요' 의 첫 두 글자는?", "a": "안녕"},
    {"q": "2의 3제곱은?", "a": "8"},
    {"q": "삼각형의 꼭짓점 개수는?", "a": "3"},
]


def generate_captcha():
    code = str(random.randint(1000, 9999))
    return code, " ─ ".join(code)


class VerificationView(discord.ui.View):
    def __init__(self, member: discord.Member, code: str, question: dict):
        super().__init__(timeout=TIMEOUT_SECONDS)
        self.member = member
        self.code = code
        self.question = question
        self.attempts = 0
        self.done = False

    @discord.ui.button(label="✅ 인증하기", style=discord.ButtonStyle.green)
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.member.id:
            await interaction.response.send_message("❌ 본인만 인증할 수 있어요.", ephemeral=True)
            return
        await interaction.response.send_modal(VerificationModal(self))

    async def on_timeout(self):
        if not self.done:
            try:
                await self.member.send("⏰ 인증 시간이 초과됐어요. 서버에서 다시 시도해주세요.")
            except (discord.Forbidden, discord.HTTPException):
                pass


class VerificationModal(discord.ui.Modal, title="🔐 서버 인증"):
    def __init__(self, view: VerificationView):
        super().__init__()
        self.view = view
        self.captcha_input = discord.ui.TextInput(
            label="캡챠 코드 입력 (숫자 4자리)",
            placeholder="화면에 표시된 숫자를 입력하세요",
            min_length=4,
            max_length=4,
        )
        self.question_input = discord.ui.TextInput(
            label=view.question["q"][:45],
            placeholder="답변을 입력하세요",
            max_length=50,
        )
        self.add_item(self.captcha_input)
        self.add_item(self.question_input)

    async def on_submit(self, interaction: discord.Interaction):
        captcha_ans = self.captcha_input.value.strip()
        question_ans = self.question_input.value.strip()
        captcha_ok = captcha_ans == self.view.code
        question = self.view.question
        question_ok = question_ans.lower() == question["a"].lower() if "a" in question else bool(question_ans)

        if captcha_ok and question_ok:
            guild = interaction.guild
            verified_role = discord.utils.get(guild.roles, name=VERIFIED_ROLE_NAME)
            unverified_role = discord.utils.get(guild.roles, name=UNVERIFIED_ROLE_NAME)
            try:
                if verified_role:
                    await self.view.member.add_roles(verified_role)
                if unverified_role:
                    await self.view.member.remove_roles(unverified_role)
            except (discord.Forbidden, discord.HTTPException) as exc:
                await interaction.response.send_message(f"❌ 역할 적용에 실패했어요: {exc}", ephemeral=True)
                return

            self.view.done = True
            self.view.stop()
            embed = discord.Embed(
                title="✅ 인증 완료!",
                description=f"{self.view.member.mention} 님, 인증됐어요! 서버를 즐겨주세요 🎮",
                color=discord.Color.green(),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            log_ch = discord.utils.get(guild.channels, name="인증-로그")
            if log_ch:
                log_embed = discord.Embed(
                    title="🔐 인증 완료",
                    description=f"{self.view.member.mention} `{self.view.member.id}`",
                    color=discord.Color.green(),
                )
                await log_ch.send(embed=log_embed)
            return

        self.view.attempts += 1
        remaining = MAX_ATTEMPTS - self.view.attempts
        if remaining <= 0:
            self.view.done = True
            self.view.stop()
            await interaction.response.send_message("❌ 인증 실패 횟수를 초과했어요. 관리자에게 문의하세요.", ephemeral=True)
            try:
                await self.view.member.kick(reason="인증 실패 초과")
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"인증 실패 자동 킥 실패 ({self.view.member.id}): {exc}")
            return

        wrong = []
        if not captcha_ok:
            wrong.append("캡챠 코드")
        if not question_ok:
            wrong.append("질문 답변")
        await interaction.response.send_message(
            f"❌ **{' / '.join(wrong)}** 가 틀렸어요. 남은 기회: **{remaining}회**",
            ephemeral=True,
        )


class Verification(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        unverified_role = discord.utils.get(guild.roles, name=UNVERIFIED_ROLE_NAME)
        if unverified_role:
            try:
                await member.add_roles(unverified_role)
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"미인증 역할 적용 실패 ({member.id}): {exc}")

        verify_ch = discord.utils.get(guild.channels, name=VERIFY_CHANNEL_NAME)
        if not verify_ch:
            return

        code, styled_code = generate_captcha()
        question = random.choice(QUESTIONS)
        embed = discord.Embed(
            title="🛡️ 서버 인증",
            description=(
                f"**{member.display_name}** 님, 환영해요!\n"
                "아래 버튼을 눌러 인증을 완료해주세요.\n\n"
                f"**캡챠 코드:**\n```\n{styled_code}\n```\n"
                f"인증 시간: **{TIMEOUT_SECONDS // 60}분**\n"
                f"최대 시도: **{MAX_ATTEMPTS}회**"
            ),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="봇이 아님을 증명하세요 🤖")
        await verify_ch.send(content=member.mention, embed=embed, view=VerificationView(member, code, question))

    @app_commands.command(name="인증설정", description="인증에 필요한 역할과 채널을 자동으로 생성합니다.")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_verification(self, interaction: discord.Interaction):
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True)
        for role_name in [VERIFIED_ROLE_NAME, UNVERIFIED_ROLE_NAME]:
            if not discord.utils.get(guild.roles, name=role_name):
                color = discord.Color.green() if role_name == VERIFIED_ROLE_NAME else discord.Color.red()
                await guild.create_role(name=role_name, color=color)
        for channel_name in [VERIFY_CHANNEL_NAME, "인증-로그"]:
            if not discord.utils.get(guild.channels, name=channel_name):
                await guild.create_text_channel(channel_name)
        await interaction.followup.send("✅ 인증 시스템 설정 완료!", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Verification(bot))
