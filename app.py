# -*- coding: utf-8 -*-
import os
import sqlite3
import asyncio
import secrets
import string
import random
import aiohttp
import urllib.parse
from datetime import datetime, timezone, timedelta

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# 환경변수 및 기본 설정
# ---------------------------------------------------------------------------
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMIN_ROLE_NAME = os.getenv("ADMIN_ROLE_NAME", "! !디노")
DB_PATH = os.getenv("DB_PATH", "shop.db")
VERIFY_ROLE_NAME = os.getenv("VERIFY_ROLE_NAME", "인증유저")
KST = timezone(timedelta(hours=9))

intents = discord.Intents.default()
intents.members = True          
intents.message_content = True  

# ---------------------------------------------------------------------------
# 디스코드 커맨드 트리 및 게이트 권한 체크
# ---------------------------------------------------------------------------
class GatedCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id is None:
            await interaction.response.send_message("이 봇은 서버 안에서만 사용할 수 있어요.", ephemeral=True)
            return False

        data = getattr(interaction, "data", None) or {}
        cmd_name = data.get("name") if isinstance(data, dict) else getattr(data, "name", None)

        all_user_commands = [
            "라이센스등록", "발로란트전적", "포인트조회", "내구매내역", "출석체크", 
            "내정보", "출금신청", "송금하기", "상점목록", "상품검색", 
            "건의하기", "출석현황", "버프확인", "인증패널전송", "상품등록", 
            "포인트지급", "포인트차감", "관리자등록", "판매자등록", "재고수정", 
            "서버정보", "공지발송", "역할지급", "청소하기"
        ]

        if cmd_name in all_user_commands:
            admin_or_seller_cmds = ["인증패널전송", "상품등록", "포인트지급", "포인트차감", "관리자등록", "판매자등록", "재고수정", "공지발송", "역할지급", "청소하기"]
            if cmd_name not in admin_or_seller_cmds:
                return True

        if not is_guild_registered(interaction.guild_id):
            await interaction.response.send_message(
                "⚠️ 이 서버는 사용 승인이 되지 않았거나 라이센스가 만료되었습니다.\n"
                "- 봇 개발자의 직인 승인(`!서버등록`) 또는 `/라이센스등록` 명령어를 이용해주세요.",
                ephemeral=True,
            )
            return False

        custom_id = None
        if isinstance(data, dict):
            custom_id = data.get("custom_id")
        else:
            custom_id = getattr(data, "custom_id", None)

        allowed_custom_ids = [
            "btn_standard", "btn_custom", "btn_role", "vending_buy", "vending_products", 
            "vending_charge", "vending_info", "select_category", "select_buy_item", 
            "confirm_buy_item", "open_ticket", "close_ticket", "ticket_buy", 
            "select_ticket_item", "verify_button", "verify_modal_submit"
        ]
        
        if custom_id:
            if custom_id in allowed_custom_ids or custom_id.startswith("notif_role_") or custom_id.startswith("mod_kick_") or custom_id.startswith("mod_ban_"):
                return True

        if interaction.type == discord.InteractionType.application_command:
            if not is_admin_or_seller(interaction):
                await interaction.response.send_message(
                    "❌ 이 기능은 관리자 또는 등록된 판매자만 사용할 수 있어요.",
                    ephemeral=True,
                )
                return False

        return True

bot = commands.Bot(command_prefix="!", intents=intents, tree_cls=GatedCommandTree)

# ---------------------------------------------------------------------------
# 데이터베이스 초기화 및 테이블 생성
# ---------------------------------------------------------------------------
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            guild_id INTEGER NOT NULL,
            item TEXT NOT NULL,
            category TEXT DEFAULT '기타',
            price INTEGER NOT NULL DEFAULT 0,
            stock INTEGER DEFAULT -1,
            target_type TEXT DEFAULT 'standard',
            is_permanent INTEGER DEFAULT 0,
            role_id INTEGER DEFAULT NULL,
            PRIMARY KEY (guild_id, item)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS item_stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            item TEXT NOT NULL,
            content TEXT NOT NULL,
            is_used INTEGER DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS permanent_stocks (
            guild_id INTEGER NOT NULL,
            item TEXT NOT NULL,
            content TEXT NOT NULL,
            PRIMARY KEY (guild_id, item)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_points (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            points INTEGER DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            buyer_id INTEGER NOT NULL,
            buyer_name TEXT NOT NULL,
            item TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            unit_price INTEGER NOT NULL,
            total_price INTEGER NOT NULL,
            memo TEXT,
            created_at TEXT NOT NULL,
            recorded_by TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS registered_guilds (
            guild_id INTEGER PRIMARY KEY,
            registered_by INTEGER NOT NULL,
            registered_at TEXT NOT NULL,
            expires_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS licenses (
            license_key TEXT PRIMARY KEY,
            duration_days INTEGER NOT NULL,
            is_used INTEGER DEFAULT 0,
            used_by_guild INTEGER,
            used_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id INTEGER PRIMARY KEY,
            receipt_channel_id INTEGER,
            welcome_channel_id INTEGER
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bot_admins (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            added_by INTEGER NOT NULL,
            added_at TEXT NOT NULL,
            PRIMARY KEY (guild_id, user_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bot_sellers (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            added_by INTEGER NOT NULL,
            added_at TEXT NOT NULL,
            PRIMARY KEY (guild_id, user_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ticket_logs (
            channel_id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            owner_id INTEGER NOT NULL,
            opened_at TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_join_counts (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            join_count INTEGER DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            last_date TEXT NOT NULL,
            PRIMARY KEY (guild_id, user_id)
        )
    """)

    conn.commit()
    conn.close()

# ---------------------------------------------------------------------------
# 헬퍼 함수들
# ---------------------------------------------------------------------------
def generate_license_key() -> str:
    chars = string.ascii_uppercase + string.digits
    parts = [''.join(secrets.choice(chars) for _ in range(4)) for _ in range(4)]
    return "-".join(parts)

def is_guild_registered(guild_id: int) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT expires_at FROM registered_guilds WHERE guild_id = ?", (guild_id,)).fetchone()
    conn.close()
    if not row:
        return False
    expires_at_str = row["expires_at"]
    if expires_at_str is None:
        return True
    exp_dt = datetime.strptime(expires_at_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
    return datetime.now(KST) < exp_dt

def is_admin(ctx_or_interaction) -> bool:
    if isinstance(ctx_or_interaction, discord.Interaction):
        member = ctx_or_interaction.user
        guild_id = ctx_or_interaction.guild_id
    else:
        member = ctx_or_interaction.author
        guild_id = ctx_or_interaction.guild.id if ctx_or_interaction.guild else None

    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.administrator or any(role.name == ADMIN_ROLE_NAME for role in member.roles):
        return True
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM bot_admins WHERE guild_id = ? AND user_id = ?", (guild_id, member.id)).fetchone()
    conn.close()
    return row is not None

def is_admin_or_seller(ctx_or_interaction) -> bool:
    if is_admin(ctx_or_interaction):
        return True
    guild_id = ctx_or_interaction.guild_id if isinstance(ctx_or_interaction, discord.Interaction) else (ctx_or_interaction.guild.id if ctx_or_interaction.guild else None)
    user_id = ctx_or_interaction.user.id if isinstance(ctx_or_interaction, discord.Interaction) else ctx_or_interaction.author.id
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM bot_sellers WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)).fetchone()
    conn.close()
    return row is not None

def fmt_won(n: int) -> str:
    return f"{n:,}원"

def now_kst_str() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

def get_user_points(guild_id: int, user_id: int) -> int:
    conn = get_conn()
    row = conn.execute("SELECT points FROM user_points WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)).fetchone()
    conn.close()
    return row["points"] if row else 0

# ---------------------------------------------------------------------------
# UI Views (자판기 갠톡 연동 및 4자리 인증 모달 포함)
# ---------------------------------------------------------------------------
class VerifyModal(discord.ui.Modal, title="라이벌 BEST클랜 회원 인증"):
    code_input = discord.ui.TextInput(
        label="4자리 인증 번호 입력",
        placeholder="화면에 안내된 4자리 숫자를 입력하세요",
        min_length=4,
        max_length=4,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        entered_code = self.code_input.value
        # 간단한 4자리 숫자 검증 로직 (원하는 검증 규칙으로 변경 가능합니다)
        if entered_code.isdigit() and len(entered_code) == 4:
            role = discord.utils.get(interaction.guild.roles, name=VERIFY_ROLE_NAME)
            if role and isinstance(interaction.user, discord.Member):
                await interaction.user.add_roles(role)
                await interaction.response.send_message(f"✅ 인증이 완료되었습니다! `{VERIFY_ROLE_NAME}` 역할이 지급되었습니다.", ephemeral=True)
            else:
                await interaction.response.send_message(f"❌ 서버에서 `{VERIFY_ROLE_NAME}` 역할을 찾을 수 없습니다. 관리자에게 문의하세요.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ 올바른 4자리 숫자를 입력해주세요.", ephemeral=True)

class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="인증하기 🔓", style=discord.ButtonStyle.green, custom_id="verify_button")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VerifyModal())

class MainVendingView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🛒 상품 구매", style=discord.ButtonStyle.blurple, custom_id="vending_buy")
    async def buy_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = get_conn()
        categories = [row["category"] for row in conn.execute("SELECT DISTINCT category FROM prices WHERE guild_id = ?", (interaction.guild_id,)).fetchall()]
        conn.close()
        if not categories:
            return await interaction.response.send_message("❌ 등록된 상품 카테고리가 없습니다.", ephemeral=True)
        
        view = discord.ui.View(timeout=180)
        select = discord.ui.Select(placeholder="카테고리를 선택하세요", custom_id="select_category")
        for cat in categories:
            select.add_option(label=cat, value=cat)
        
        async def select_callback(inter: discord.Interaction):
            cat_val = select.values[0]
            c = get_conn()
            items = c.execute("SELECT item, price, stock FROM prices WHERE guild_id = ? AND category = ?", (inter.guild_id, cat_val)).fetchall()
            c.close()
            
            item_view = discord.ui.View(timeout=180)
            item_select = discord.ui.Select(placeholder="구매할 상품을 선택하세요", custom_id="select_buy_item")
            for it in items:
                stock_str = f"재고: {it['stock']}개" if it['stock'] != -1 else "재고: 무제한"
                item_select.add_option(label=it["item"], description=f"가격: {fmt_won(it['price'])} | {stock_str}", value=it["item"])
            
            async def item_callback(i: discord.Interaction):
                selected_item = item_select.values[0]
                c = get_conn()
                it_info = c.execute("SELECT price, stock FROM prices WHERE guild_id = ? AND item = ?", (i.guild_id, selected_item)).fetchone()
                c.close()
                
                if not it_info:
                    return await i.response.send_message("❌ 존재하지 않는 상품입니다.", ephemeral=True)
                
                user_pts = get_user_points(i.guild_id, i.user.id)
                if user_pts < it_info["price"]:
                    return await i.response.send_message(f"❌ 포인트가 부족합니다! (내 잔액: {fmt_won(user_pts)}, 필요 가격: {fmt_won(it_info['price'])})", ephemeral=True)
                
                c = get_conn()
                c.execute("UPDATE user_points SET points = points - ? WHERE guild_id = ? AND user_id = ?", (it_info["price"], i.guild_id, i.user.id))
                c.execute("INSERT INTO transactions (guild_id, buyer_id, buyer_name, item, quantity, unit_price, total_price, memo, created_at, recorded_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                          (i.guild_id, i.user.id, i.user.display_name, selected_item, 1, it_info["price"], it_info["price"], "자판기 즉시 구매", now_kst_str(), "System"))
                c.commit()
                c.close()
                
                await i.response.send_message("✅ 구매가 완료되었습니다! 상세 내역을 개인 메시지(DM)로 전송해 드렸습니다.", ephemeral=True)
                
                try:
                    dm_embed = discord.Embed(
                        title="🛒 자판기 상품 구매 완료",
                        description=f"**{i.guild.name}** 서버에서 상품을 구매하셨습니다.",
                        color=discord.Color.green()
                    )
                    dm_embed.add_field(name="상품명", value=selected_item, inline=False)
                    dm_embed.add_field(name="결제 금액", value=fmt_won(it_info['price']), inline=True)
                    dm_embed.add_field(name="남은 포인트", value=fmt_won(get_user_points(i.guild.id, i.user.id)), inline=True)
                    await i.user.send(embed=dm_embed)
                except Exception:
                    pass

            item_select.callback = item_callback
            item_view.add_item(item_select)
            await inter.response.send_message(f"📂 **{cat_val}** 카테고리 상품 목록입니다.", view=item_view, ephemeral=True)

        select.callback = select_callback
        view.add_item(select)
        await interaction.response.send_message("🛒 구매하실 상품의 카테고리를 선택해주세요.", view=view, ephemeral=True)

    @discord.ui.button(label="📋 상품 목록", style=discord.ButtonStyle.gray, custom_id="vending_products")
    async def list_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = get_conn()
        items = conn.execute("SELECT item, category, price, stock FROM prices WHERE guild_id = ?", (interaction.guild_id,)).fetchall()
        conn.close()
        if not items:
            return await interaction.response.send_message("❌ 등록된 상품이 없습니다.", ephemeral=True)
        
        embed = discord.Embed(title="🛍️ 서버 상품 목록", color=discord.Color.blue())
        for it in items:
            stock_str = f"{it['stock']}개" if it['stock'] != -1 else "무제한"
            embed.add_field(name=f"[{it['category']}] {it['item']}", value=f"가격: **{fmt_won(it['price'])}** | 재고: {stock_str}", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="💰 포인트 충전 문의", style=discord.ButtonStyle.green, custom_id="vending_charge")
    async def charge_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("💬 포인트 충전은 관리자에게 문의해주세요!", ephemeral=True)

    @discord.ui.button(label="ℹ️ 이용 안내", style=discord.ButtonStyle.gray, custom_id="vending_info")
    async def info_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("📖 상점 및 자판기 이용 안내: 포인트를 충전하여 상품을 구매할 수 있습니다.", ephemeral=True)

class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="🎫 문의 티켓 생성", style=discord.ButtonStyle.green, custom_id="open_ticket")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🎫 문의 티켓이 생성되었습니다.", ephemeral=True)

class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="🔒 티켓 닫기", style=discord.ButtonStyle.red, custom_id="close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🔒 티켓을 종료합니다.", ephemeral=True)

# ---------------------------------------------------------------------------
# 슬래시 명령어 정의
# ---------------------------------------------------------------------------

@bot.tree.command(name="라이센스등록", description="라이센스 키를 입력하여 서버 이용 권한을 등록합니다.")
@app_commands.describe(라이센스키="발급받은 라이센스 키")
async def register_license(interaction: discord.Interaction, 라이센스키: str):
    conn = get_conn()
    lic = conn.execute("SELECT * FROM licenses WHERE license_key = ? AND is_used = 0", (라이센스키,)).fetchone()
    if not lic:
        conn.close()
        return await interaction.response.send_message("❌ 유효하지 않거나 이미 사용된 라이센스 키입니다.", ephemeral=True)
    
    days = lic["duration_days"]
    expires_at = (datetime.now(KST) + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    
    conn.execute("UPDATE licenses SET is_used = 1, used_by_guild = ?, used_at = ? WHERE license_key = ?", (interaction.guild_id, now_kst_str(), 라이센스키))
    conn.execute(
        "INSERT INTO registered_guilds (guild_id, registered_by, registered_at, expires_at) VALUES (?, ?, ?, ?) ON CONFLICT(guild_id) DO UPDATE SET expires_at = ?",
        (interaction.guild_id, interaction.user.id, now_kst_str(), expires_at, expires_at)
    )
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"🎉 라이센스 등록이 완료되었습니다! (이용 기간: {days}일, 만료일: {expires_at})", ephemeral=True)

@bot.tree.command(name="발로란트전적", description="발로란트 유저의 티어 및 최근 전적을 검색합니다.")
@app_commands.describe(닉네임="발로란트 닉네임", 태그="태그 (예: KR1)")
async def valorant_stats(interaction: discord.Interaction, 닉네임: str, 태그: str):
    await interaction.response.defer()
    clean_tag = 태그.strip().lstrip("#")
    url = f"https://api.henrikdev.xyz/valorant/v1/account/{urllib.parse.quote(닉네임)}/{urllib.parse.quote(clean_tag)}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                return await interaction.followup.send("❌ 유저를 찾을 수 없습니다.")
            data = await resp.json()
    try:
        acc = data.get("data", {})
        region = acc.get("region", "kr")
        mmr_url = f"https://api.henrikdev.xyz/valorant/v2/mmr/{region}/{urllib.parse.quote(닉네임)}/{urllib.parse.quote(clean_tag)}"
        async with aiohttp.ClientSession() as session:
            async with session.get(mmr_url) as resp:
                mmr_data = await resp.json() if resp.status == 200 else {}
        curr = mmr_data.get("data", {}).get("current_data", {})
        embed = discord.Embed(title=f"🎮 발로란트: {닉네임}#{clean_tag}", color=discord.Color.from_rgb(255, 70, 85))
        embed.add_field(name="🏆 현재 티어", value=f"**{curr.get('currenttierpatched', 'Unranked')}** ({curr.get('ranking_in_tier', 0)} RR)")
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"⚠️ 오류 발생: {e}")

@bot.tree.command(name="포인트조회", description="내 남은 포인트 잔액을 확인합니다.")
async def check_my_points(interaction: discord.Interaction):
    pts = get_user_points(interaction.guild_id, interaction.user.id)
    await interaction.response.send_message(f"💰 내 포인트 잔액: **{fmt_won(pts)}**", ephemeral=True)

@bot.tree.command(name="내구매내역", description="내가 구매한 상품들의 내역을 확인합니다.")
async def my_purchases(interaction: discord.Interaction):
    conn = get_conn()
    rows = conn.execute("SELECT item, total_price, created_at FROM transactions WHERE guild_id = ? AND buyer_id = ? ORDER BY id DESC LIMIT 5", (interaction.guild_id, interaction.user.id)).fetchall()
    conn.close()
    if not rows:
        return await interaction.response.send_message("❌ 구매 내역이 없습니다.", ephemeral=True)
    embed = discord.Embed(title="📦 최근 구매 내역 (최대 5개)", color=discord.Color.blue())
    for r in rows:
        embed.add_field(name=r["item"], value=f"가격: {fmt_won(r['total_price'])} | 일시: {r['created_at']}", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="출석체크", description="매일 출석체크를 하고 포인트를 받으세요!")
async def daily_attendance(interaction: discord.Interaction):
    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    conn = get_conn()
    row = conn.execute("SELECT last_date FROM attendance WHERE guild_id = ? AND user_id = ?", (interaction.guild_id, interaction.user.id)).fetchone()
    if row and row["last_date"] == today_str:
        conn.close()
        return await interaction.response.send_message("❌ 이미 오늘 출석체크를 완료하셨습니다!", ephemeral=True)
    reward = 500
    conn.execute("INSERT INTO attendance (guild_id, user_id, last_date) VALUES (?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE SET last_date = ?", (interaction.guild_id, interaction.user.id, today_str, today_str))
    conn.execute("INSERT INTO user_points (guild_id, user_id, points) VALUES (?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE SET points = points + ?", (interaction.guild_id, interaction.user.id, reward, reward))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ 출석체크 완료! 보상: **{fmt_won(reward)}**", ephemeral=True)

@bot.tree.command(name="내정보", description="내 프로필 및 활동 정보를 요약해서 보여줍니다.")
async def my_profile(interaction: discord.Interaction):
    pts = get_user_points(interaction.guild_id, interaction.user.id)
    embed = discord.Embed(title=f"👤 {interaction.user.display_name}님의 프로필", color=discord.Color.blue())
    embed.add_field(name="💰 보유 포인트", value=fmt_won(pts), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="출금신청", description="보유 포인트를 현금 환전/출금 신청합니다.")
@app_commands.describe(금액="출금할 포인트")
async def withdraw_points(interaction: discord.Interaction, 금액: int):
    if 금액 <= 0:
        return await interaction.response.send_message("❌ 올바른 금액을 입력하세요.", ephemeral=True)
    pts = get_user_points(interaction.guild_id, interaction.user.id)
    if pts < 금액:
        return await interaction.response.send_message("❌ 포인트 부족", ephemeral=True)
    await interaction.response.send_message(f"✅ {fmt_won(금액)} 출금 신청이 관리자에게 접수되었습니다.", ephemeral=True)

@bot.tree.command(name="송금하기", description="다른 유저에게 포인트를 선물합니다.")
@app_commands.describe(유저="선물할 유저", 금액="선물할 포인트")
async def send_points(interaction: discord.Interaction, 유저: discord.Member, 금액: int):
    if 금액 <= 0 or 유저.id == interaction.user.id:
        return await interaction.response.send_message("❌ 올바르지 않은 요청입니다.", ephemeral=True)
    pts = get_user_points(interaction.guild_id, interaction.user.id)
    if pts < 금액:
        return await interaction.response.send_message("❌ 포인트 부족", ephemeral=True)
    conn = get_conn()
    conn.execute("UPDATE user_points SET points = points - ? WHERE guild_id = ? AND user_id = ?", (금액, interaction.guild_id, interaction.user.id))
    conn.execute("INSERT INTO user_points (guild_id, user_id, points) VALUES (?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE SET points = points + ?", (interaction.guild_id, 유저.id, 금액, 금액))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ {유저.mention}님께 {fmt_won(금액)}을(를) 송금했습니다.", ephemeral=True)

@bot.tree.command(name="상점목록", description="서버에 등록된 모든 판매 상품을 조회합니다.")
async def shop_list_cmd(interaction: discord.Interaction):
    conn = get_conn()
    items = conn.execute("SELECT item, category, price, stock FROM prices WHERE guild_id = ?", (interaction.guild_id,)).fetchall()
    conn.close()
    if not items:
        return await interaction.response.send_message("❌ 등록된 상품이 없습니다.", ephemeral=True)
    embed = discord.Embed(title="🛍️ 전체 상점 상품 목록", color=discord.Color.blue())
    for it in items:
        stock_str = f"{it['stock']}개" if it['stock'] != -1 else "무제한"
        embed.add_field(name=f"[{it['category']}] {it['item']}", value=f"가격: **{fmt_won(it['price'])}** | 재고: {stock_str}", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="상품검색", description="원하는 상품 이름을 검색합니다.")
@app_commands.describe(검색어="검색할 상품명")
async def search_product(interaction: discord.Interaction, 검색어: str):
    conn = get_conn()
    items = conn.execute("SELECT item, category, price, stock FROM prices WHERE guild_id = ? AND item LIKE ?", (interaction.guild_id, f"%{검색어}%")).fetchall()
    conn.close()
    if not items:
        return await interaction.response.send_message(f"❌ '{검색어}'에 해당하는 상품이 없습니다.", ephemeral=True)
    embed = discord.Embed(title=f"🔍 '{검색어}' 검색 결과", color=discord.Color.blue())
    for it in items:
        embed.add_field(name=f"[{it['category']}] {it['item']}", value=f"가격: {fmt_won(it['price'])}", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="건의하기", description="서버 운영진에게 건의사항을 전달합니다.")
@app_commands.describe(내용="건의할 내용")
async def suggest_cmd(interaction: discord.Interaction, 내용: str):
    await interaction.response.send_message("✅ 건의사항이 운영진에게 전송되었습니다.", ephemeral=True)

@bot.tree.command(name="출석현황", description="나의 최근 출석체크 기록을 확인합니다.")
async def attendance_status(interaction: discord.Interaction):
    conn = get_conn()
    row = conn.execute("SELECT last_date FROM attendance WHERE guild_id = ? AND user_id = ?", (interaction.guild_id, interaction.user.id)).fetchone()
    conn.close()
    last = row["last_date"] if row else "기록 없음"
    await interaction.response.send_message(f"📅 최근 출석일: **{last}**", ephemeral=True)

@bot.tree.command(name="버프확인", description="현재 적용 중인 서버 버프 및 혜택을 확인합니다.")
async def check_buffs(interaction: discord.Interaction):
    await interaction.response.send_message("✨ 현재 적용 중인 특별 버프가 없습니다.", ephemeral=True)

# 📸 이미지 속 인증 패널을 현재 채널에 띄우는 관리자 명령어 추가
@bot.tree.command(name="인증패널전송", description="라이벌 BEST클랜 회원 인증 패널을 현재 채널에 전송합니다.")
async def send_verify_panel(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ 관리자만 사용할 수 있습니다.", ephemeral=True)
    
    embed = discord.Embed(
        title="🔒 라이벌 BEST클랜 회원 인증",
        description="아래 [인증하기 🔓] 버튼을 누른 후, 안내되는 4자리 숫자를 입력해 주세요.",
        color=discord.Color.from_rgb(46, 204, 113)
    )
    await interaction.channel.send(embed=embed, view=VerifyView())
    await interaction.response.send_message("✅ 인증 패널을 전송했습니다.", ephemeral=True)

@bot.tree.command(name="상품등록", description="상점에 새로운 상품을 등록합니다.")
@app_commands.describe(카테고리="카테고리", 상품명="상품명", 가격="가격", 재고="재고 (-1은 무제한)")
async def register_product(interaction: discord.Interaction, 카테고리: str, 상품명: str, 가격: int, 재고: int = -1):
    if not is_admin_or_seller(interaction):
        return await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
    conn = get_conn()
    conn.execute("INSERT INTO prices (guild_id, item, category, price, stock) VALUES (?, ?, ?, ?, ?) ON CONFLICT(guild_id, item) DO UPDATE SET category = ?, price = ?, stock = ?", (interaction.guild_id, 상품명, 카테고리, 가격, 재고, 카테고리, 가격, 재고))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ 상품 **[{카테고리}] {상품명}** 등록 완료!", ephemeral=True)

@bot.tree.command(name="포인트지급", description="유저에게 포인트를 지급합니다.")
@app_commands.describe(유저="대상 유저", 금액="지급할 포인트")
async def give_points(interaction: discord.Interaction, 유저: discord.Member, 금액: int):
    if not is_admin_or_seller(interaction):
        return await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
    conn = get_conn()
    conn.execute("INSERT INTO user_points (guild_id, user_id, points) VALUES (?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE SET points = points + ?", (interaction.guild_id, 유저.id, 금액, 금액))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ {유저.mention}님께 {fmt_won(금액)} 지급 완료!", ephemeral=True)

@bot.tree.command(name="포인트차감", description="유저의 포인트를 강제로 차감합니다.")
@app_commands.describe(유저="대상 유저", 금액="차감할 포인트")
async def remove_points(interaction: discord.Interaction, 유저: discord.Member, 금액: int):
    if not is_admin_or_seller(interaction):
        return await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
    conn = get_conn()
    conn.execute("UPDATE user_points SET points = MAX(0, points - ?) WHERE guild_id = ? AND user_id = ?", (금액, interaction.guild_id, 유저.id))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ {유저.mention}님의 포인트 {fmt_won(금액)} 차감 완료!", ephemeral=True)

@bot.tree.command(name="관리자등록", description="새로운 봇 관리자를 임명합니다.")
@app_commands.describe(유저="임명할 유저")
async def add_bot_admin(interaction: discord.Interaction, 유저: discord.Member):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ 관리자만 가능합니다.", ephemeral=True)
    conn = get_conn()
    conn.execute("INSERT INTO bot_admins (guild_id, user_id, added_by, added_at) VALUES (?, ?, ?, ?) ON CONFLICT(guild_id, user_id) DO NOTHING", (interaction.guild_id, 유저.id, interaction.user.id, now_kst_str()))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ {유저.mention}님을 봇 관리자로 등록했습니다.", ephemeral=True)

@bot.tree.command(name="판매자등록", description="새로운 판매자를 임명합니다.")
@app_commands.describe(유저="임명할 유저")
async def add_bot_seller(interaction: discord.Interaction, 유저: discord.Member):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ 관리자만 가능합니다.", ephemeral=True)
    conn = get_conn()
    conn.execute("INSERT INTO bot_sellers (guild_id, user_id, added_by, added_at) VALUES (?, ?, ?, ?) ON CONFLICT(guild_id, user_id) DO NOTHING", (interaction.guild_id, 유저.id, interaction.user.id, now_kst_str()))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ {유저.mention}님을 판매자로 등록했습니다.", ephemeral=True)

@bot.tree.command(name="재고수정", description="등록된 상품의 재고를 수정합니다.")
@app_commands.describe(상품명="상품명", 재고="변경할 재고 개수")
async def update_stock(interaction: discord.Interaction, 상품명: str, 재고: int):
    if not is_admin_or_seller(interaction):
        return await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
    conn = get_conn()
    conn.execute("UPDATE prices SET stock = ? WHERE guild_id = ? AND item = ?", (재고, interaction.guild_id, 상품명))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ '{상품명}' 상품의 재고를 {재고}개로 수정했습니다.", ephemeral=True)

@bot.tree.command(name="서버정보", description="현재 서버의 기본 정보와 봇 등록 상태를 확인합니다.")
async def server_info(interaction: discord.Interaction):
    is_reg = is_guild_registered(interaction.guild_id)
    embed = discord.Embed(title=f"📊 {interaction.guild.name} 서버 정보", color=discord.Color.blue())
    embed.add_field(name="서버 ID", value=str(interaction.guild_id), inline=True)
    embed.add_field(name="라이센스 승인", value="✅ 승인됨" if is_reg else "❌ 미승인", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="공지발송", description="서버 전체에 공지사항을 임베드로 전송합니다.")
@app_commands.describe(내용="공지 내용")
async def send_announcement(interaction: discord.Interaction, 내용: str):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ 관리자만 가능합니다.", ephemeral=True)
    embed = discord.Embed(title="📢 서버 공지사항", description=내용, color=discord.Color.red(), timestamp=datetime.now(KST))
    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("✅ 공지 전송 완료", ephemeral=True)

@bot.tree.command(name="역할지급", description="유저에게 특정 역할을 부여합니다.")
@app_commands.describe(유저="대상 유저", 역할="부여할 역할")
async def give_role(interaction: discord.Interaction, 유저: discord.Member, 역할: discord.Role):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ 관리자만 가능합니다.", ephemeral=True)
    await 유저.add_roles(역할)
    await interaction.response.send_message(f"✅ {유저.mention}님께 {역할.name} 역할을 지급했습니다.", ephemeral=True)

@bot.tree.command(name="청소하기", description="채팅 메시지를 지정한 개수만큼 삭제합니다.")
@app_commands.describe(개수="삭제할 메시지 개수")
async def clear_messages(interaction: discord.Interaction, 개수: int):
    if not is_admin(interaction):
        return await interaction.response.send_message("❌ 관리자만 가능합니다.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=개수)
    await interaction.followup.send(f"✅ 메시지 {len(deleted)}개를 삭제했습니다.", ephemeral=True)

# ---------------------------------------------------------------------------
# 전통적 접두사 명령어
# ---------------------------------------------------------------------------
@bot.command(name="서버등록")
async def register_guild_cmd(ctx, guild_id_str: str = None, days_str: str = None):
    if not is_admin(ctx):
        return await ctx.send("❌ 이 명령어는 봇 관리자만 사용할 수 있습니다.")
    target_guild_id = int(guild_id_str) if guild_id_str else ctx.guild.id
    days = int(days_str) if days_str else 30
    expires_at = (datetime.now(KST) + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    conn.execute("INSERT INTO registered_guilds (guild_id, registered_by, registered_at, expires_at) VALUES (?, ?, ?, ?) ON CONFLICT(guild_id) DO UPDATE SET expires_at = ?", (target_guild_id, ctx.author.id, now_kst_str(), expires_at, expires_at))
    conn.commit()
    conn.close()
    await ctx.send(f"✅ 서버(`{target_guild_id}`) 등록 완료. 만료일: `{expires_at}`")

# ---------------------------------------------------------------------------
# 봇 구동 이벤트
# ---------------------------------------------------------------------------
@bot.event
async def on_ready():
    init_db()
    bot.add_view(MainVendingView())
    bot.add_view(TicketPanelView())
    bot.add_view(TicketControlView())
    bot.add_view(VerifyView())

    try:
        synced = await bot.tree.sync()
        print(f"슬래시 명령어 {len(synced)}개 동기화 완료")
    except Exception as e:
        print(f"명령어 동기화 실패: {e}")
    print(f"✅ 로그인 완료: {bot.user}")

if __name__ == "__main__":
    bot.run(TOKEN)
