import discord
from discord.ext import commands
from discord import app_commands
import random
import asyncio
import json
import os

# ── 설정 ──────────────────────────────────────────────
VERIFIED_ROLE_NAME = "인증완료"          # 인증 후 부여할 역할 이름
UNVERIFIED_ROLE_NAME = "미인증"          # 입장 시 부여할 역할 이름
VERIFY_CHANNEL_NAME = "인증"            # 인증 채널 이름
MAX_ATTEMPTS = 3                        # 최대 틀릴 수 있는 횟수
TIMEOUT_SECONDS = 120                   # 인증 제한 시간 (초)

# 4자리 질문형 캡챠 문제 풀 (원하는 만큼 추가 가능)
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

# 4자리 숫자 캡챠
def generate_captcha():
    """4자리 숫자 생성 + 이미지 텍스트 형태로 표시"""
    code = str(random.randint(1000, 9999))
    # 아스키 아트로 숫자 표현 (이미지 대신)
    styled = " ─ ".join(list(code))
    return code, styled

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
            except:
                pass

class VerificationModal(discord.ui.Modal, title="🔐 서버 인증"):
    def __init__(self, view: VerificationView):
        super().__init__()
        self.view = view
        self.captcha_input = discord.ui.TextInput(
            label=f"캡챠 코드 입력 (숫자 4자리)",
            placeholder="화면에 표시된 숫자를 입력하세요",
            min_length=4,
            max_length=4,
        )
        self.question_input = discord.ui.TextInput(
            label=view.question["q"][:45],  # Discord 라벨 45자 제한
            placeholder="답변을 입력하세요",
            max_length=50,
        )
        self.add_item(self.captcha_input)
        self.add_item(self.question_input)

    async def on_submit(self, interaction: discord.Interaction):
        captcha_ans = self.captcha_input.value.strip()
        question_ans = self.question_input.value.strip()

        captcha_ok = captcha_ans == self.view.code
        # 질문 답 검증 (타입이 text면 정답 키 없음 → 관리자가 직접 확인 or 통과)
        q = self.view.question
        if "a" in q:
            question_ok = question_ans.lower() == q["a"].lower()
        else:
            question_ok = len(question_ans) >= 1  # 텍스트형은 내용만 있으면 통과

        if captcha_ok and question_ok:
            guild = interaction.guild
            verified_role = discord.utils.get(guild.roles, name=VERIFIED_ROLE_NAME)
            unverified_role = discord.utils.get(guild.roles, name=UNVERIFIED_ROLE_NAME)

            if verified_role:
                await self.view.member.add_roles(verified_role)
            if unverified_role:
                await self.view.member.remove_roles(unverified_role)

            self.view.done = True
            self.view.stop()

            embed = discord.Embed(
                title="✅ 인증 완료!",
                description=f"{self.view.member.mention} 님, 인증됐어요! 서버를 즐겨주세요 🎮",
                color=discord.Color.green()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

            # 로그 채널에 기록
            log_ch = discord.utils.get(guild.channels, name="인증-로그")
            if log_ch:
                log_embed = discord.Embed(
                    title="🔐 인증 완료",
                    description=f"{self.view.member.mention} `{self.view.member.id}`",
                    color=discord.Color.green()
                )
                await log_ch.send(embed=log_embed)
        else:
            self.view.attempts += 1
            remaining = MAX_ATTEMPTS - self.view.attempts
            if remaining <= 0:
                self.view.done = True
                self.view.stop()
                await interaction.response.send_message(
                    "❌ 인증 실패 횟수를 초과했어요. 관리자에게 문의하세요.", ephemeral=True
                )
                try:
                    await self.view.member.kick(reason="인증 실패 초과")
                except:
                    pass
            else:
                msg = []
                if not captcha_ok:
                    msg.append("캡챠 코드")
                if not question_ok:
                    msg.append("질문 답변")
                await interaction.response.send_message(
                    f"❌ **{' / '.join(msg)}** 가 틀렸어요. 남은 기회: **{remaining}회**",
                    ephemeral=True
                )


class Verification(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild

        # 미인증 역할 부여
        unverified_role = discord.utils.get(guild.roles, name=UNVERIFIED_ROLE_NAME)
        if unverified_role:
            await member.add_roles(unverified_role)

        # 인증 채널 찾기
        verify_ch = discord.utils.get(guild.channels, name=VERIFY_CHANNEL_NAME)
        if not verify_ch:
            return

        # 캡챠 + 랜덤 질문 생성
        code, styled_code = generate_captcha()
        question = random.choice(QUESTIONS)

        embed = discord.Embed(
            title="🛡️ 서버 인증",
            description=(
                f"**{member.display_name}** 님, 환영해요!\n"
                f"아래 버튼을 눌러 인증을 완료해주세요.\n\n"
                f"**캡챠 코드:**\n```\n{styled_code}\n```\n"
                f"인증 시간: **{TIMEOUT_SECONDS // 60}분**\n"
                f"최대 시도: **{MAX_ATTEMPTS}회**"
            ),
            color=discord.Color.blurple()
        )
        embed.set_footer(text="봇이 아님을 증명하세요 🤖")

        view = VerificationView(member, code, question)
        await verify_ch.send(content=member.mention, embed=embed, view=view)

    @app_commands.command(name="인증설정", description="인증에 필요한 역할과 채널을 자동으로 생성합니다.")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_verification(self, interaction: discord.Interaction):
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True)

        # 역할 생성
        for role_name in [VERIFIED_ROLE_NAME, UNVERIFIED_ROLE_NAME]:
            if not discord.utils.get(guild.roles, name=role_name):
                color = discord.Color.green() if role_name == VERIFIED_ROLE_NAME else discord.Color.red()
                await guild.create_role(name=role_name, color=color)

        # 채널 생성
        for ch_name in [VERIFY_CHANNEL_NAME, "인증-로그"]:
            if not discord.utils.get(guild.channels, name=ch_name):
                await guild.create_text_channel(ch_name)

        await interaction.followup.send("✅ 인증 시스템 설정 완료!", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Verification(bot))
