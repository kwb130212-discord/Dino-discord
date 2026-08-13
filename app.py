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

        # 기존 유저용 명령어 + 새로 추가될 유저용 명령어 이름들을 여기에 추가하면 체크를 통과합니다.
        if cmd_name in ["라이센스등록", "발로란트전적", "검강화", "내검정보", "주사위도박", "코인토스", "포인트조회", "내구매내역", "출석체크"]:
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
            "select_ticket_item", "verify_button", "sword_enhance"
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
        CREATE TABLE IF NOT EXISTS user_swords (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            level INTEGER DEFAULT 0,
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
    # ── [추가 기능 테이블] 출석체크 테이블 추가 ──────────────────────────
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
# UI Views (상점, 자판기, 티켓, 인증 패널)
# ---------------------------------------------------------------------------
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
                
                await i.response.send_message(f"✅ **{selected_item}** 구매가 완료되었습니다! (차감 포인트: {fmt_won(it_info['price'])})", ephemeral=True)

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
        await interaction.response.send_message("📖 상점 및 자판기 이용 안내: 포인트를 충전하여 상품을 구매하거나 검 강화, 도박을 즐길 수 있습니다.", ephemeral=True)

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

class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="✅ 인증하기", style=discord.ButtonStyle.green, custom_id="verify_button")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        role = discord.utils.get(interaction.guild.roles, name=VERIFY_ROLE_NAME)
        if role and isinstance(interaction.user, discord.Member):
            await interaction.user.add_roles(role)
            await interaction.response.send_message("✅ 인증이 완료되었습니다!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ 인증 역할을 찾을 수 없습니다.", ephemeral=True)

# ---------------------------------------------------------------------------
# ⚔️ 검 강화 시스템 명령어 및 뷰
# ---------------------------------------------------------------------------
SWORD_NAMES = {
    0: "녹슨 단검", 1: "철검", 2: "기사 대검", 3: "화염의 검", 4: "서릿발 검",
    5: "폭풍의 검", 6: "드래곤 슬레이어", 7: "빛의 성검", 8: "어둠의 마검", 9: "태초의 검", 10: "🌟 [신화] 천상검 🌟"
}

def get_enhance_cost(level: int) -> int:
    return 1000 * (level + 1)

def get_enhance_chance(level: int) -> int:
    chances = {0: 95, 1: 85, 2: 75, 3: 65, 4: 55, 5: 45, 6: 35, 7: 25, 8: 15, 9: 10}
    return chances.get(level, 5)

class SwordEnhanceView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="⚔️ 강화 시도하기", style=discord.ButtonStyle.green, custom_id="sword_enhance")
    async def enhance_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = interaction.guild_id
        user_id = interaction.user.id

        conn = get_conn()
        sword = conn.execute("SELECT level FROM user_swords WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)).fetchone()
        current_level = sword["level"] if sword else 0

        if current_level >= 10:
            conn.close()
            return await interaction.response.send_message("✨ 이미 최고 단계(+10)의 신화 검을 달성하셨습니다!", ephemeral=True)

        cost = get_enhance_cost(current_level)
        pts = get_user_points(guild_id, user_id)

        if pts < cost:
            conn.close()
            return await interaction.response.send_message(f"❌ 포인트가 부족합니다! (필요 포인트: {fmt_won(cost)}, 내 잔액: {fmt_won(pts)})", ephemeral=True)

        chance = get_enhance_chance(current_level)
        roll = random.randint(1, 100)

        conn.execute("UPDATE user_points SET points = points - ? WHERE guild_id = ? AND user_id = ?", (cost, guild_id, user_id))

        if roll <= chance:
            new_level = current_level + 1
            conn.execute(
                "INSERT INTO user_swords (guild_id, user_id, level) VALUES (?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE SET level = ?",
                (guild_id, user_id, new_level, new_level)
            )
            conn.commit()
            conn.close()
            
            embed = discord.Embed(
                title="🎉 강화 성공!",
                description=f"축하합니다! 검 강화에 **성공**했습니다.\n\n"
                            f"• 기존: `+{current_level} {SWORD_NAMES.get(current_level, '검')}`\n"
                            f"• 현재: `+{new_level} {SWORD_NAMES.get(new_level, '검')}`\n"
                            f"• 소모 비용: `{fmt_won(cost)}`",
                color=discord.Color.green()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            new_level = max(0, current_level - 1)
            conn.execute(
                "INSERT INTO user_swords (guild_id, user_id, level) VALUES (?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE SET level = ?",
                (guild_id, user_id, new_level, new_level)
            )
            conn.commit()
            conn.close()

            embed = discord.Embed(
                title="💥 강화 실패...",
                description=f"아쉽게도 강화에 실패하여 검이 손상되었습니다...\n\n"
                            f"• 현재 단계: `+{new_level} {SWORD_NAMES.get(new_level, '검')}`\n"
                            f"• 소모 비용: `{fmt_won(cost)}`",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="내검정보", description="내 검의 현재 강화 단계와 다음 강화 비용을 확인합니다.")
async def my_sword_info(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    user_id = interaction.user.id
    conn = get_conn()
    sword = conn.execute("SELECT level FROM user_swords WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)).fetchone()
    conn.close()

    lvl = sword["level"] if sword else 0
    cost = get_enhance_cost(lvl) if lvl < 10 else 0
    chance = get_enhance_chance(lvl) if lvl < 10 else 0

    embed = discord.Embed(
        title=f"⚔️ {interaction.user.display_name}님의 대장간 인벤토리",
        description=f"• **보유 검:** `+{lvl} {SWORD_NAMES.get(lvl, '검')}`\n"
                    f"• **다음 강화 비용:** `{fmt_won(cost)}`\n"
                    f"• **다음 단계 성공 확률:** `{chance}%`",
        color=discord.Color.orange()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True, view=SwordEnhanceView())

# ---------------------------------------------------------------------------
# 🎲 도박 시스템 명령어
# ---------------------------------------------------------------------------
@bot.tree.command(name="주사위도박", description="봇과 주사위 숫자를 겨뤄서 이기면 포인트를 2배로 획득합니다!")
@app_commands.describe(베팅금액="걸고 싶은 포인트")
async def dice_gambling(interaction: discord.Interaction, 베팅금액: int):
    guild_id = interaction.guild_id
    user_id = interaction.user.id

    if 베팅금액 <= 0:
        return await interaction.response.send_message("❌ 1원 이상부터 베팅할 수 있습니다.", ephemeral=True)

    pts = get_user_points(guild_id, user_id)
    if pts < 베팅금액:
        return await interaction.response.send_message(f"❌ 포인트가 부족합니다! (내 잔액: {fmt_won(pts)})", ephemeral=True)

    conn = get_conn()
    user_dice = random.randint(1, 6)
    bot_dice = random.randint(1, 6)

    if user_dice > bot_dice:
        conn.execute("UPDATE user_points SET points = points + ? WHERE guild_id = ? AND user_id = ?", (베팅금액, guild_id, user_id))
        conn.commit()
        result_text = f"🎉 **승리하셨습니다!** (`+{fmt_won(베팅금액)}` 획득)"
        color = discord.Color.green()
    elif user_dice < bot_dice:
        conn.execute("UPDATE user_points SET points = points - ? WHERE guild_id = ? AND user_id = ?", (베팅금액, guild_id, user_id))
        conn.commit()
        result_text = f"😢 **패배하셨습니다...** (`-{fmt_won(베팅금액)}` 차감)"
        color = discord.Color.red()
    else:
        result_text = "🤝 **무승부!** 포인트를 돌려드립니다."
        color = discord.Color.gold()
    
    new_pts = get_user_points(guild_id, user_id)
    conn.close()

    embed = discord.Embed(title="🎲 주사위 도박 결과", description=result_text, color=color)
    embed.add_field(name="내 주사위", value=f"🎲 **{user_dice}**", inline=True)
    embed.add_field(name="봇 주사위", value=f"🎲 **{bot_dice}**", inline=True)
    embed.add_field(name="남은 잔액", value=f"💰 `{fmt_won(new_pts)}`", inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="코인토스", description="동전 앞면/뒷면을 맞춰서 포인트를 2배로 불려보세요!")
@app_commands.describe(선택="앞면 또는 뒷면 중 선택", 베팅금액="걸고 싶은 포인트")
@app_commands.choices(선택=[
    app_commands.Choice(name="앞면", value="앞면"),
    app_commands.Choice(name="뒷면", value="뒷면")
])
async def coin_toss(interaction: discord.Interaction, 선택: str, 베팅금액: int):
    guild_id = interaction.guild_id
    user_id = interaction.user.id

    if 베팅금액 <= 0:
        return await interaction.response.send_message("❌ 1원 이상부터 베팅할 수 있습니다.", ephemeral=True)

    pts = get_user_points(guild_id, user_id)
    if pts < 베팅금액:
        return await interaction.response.send_message(f"❌ 포인트가 부족합니다! (내 잔액: {fmt_won(pts)})", ephemeral=True)

    conn = get_conn()
    result_coin = random.choice(["앞면", "뒷면"])

    if 선택 == result_coin:
        conn.execute("UPDATE user_points SET points = points + ? WHERE guild_id = ? AND user_id = ?", (베팅금액, guild_id, user_id))
        conn.commit()
        result_text = f"🪙 **정답입니다!** (`+{fmt_won(베팅금액)}` 획득)"
        color = discord.Color.green()
    else:
        conn.execute("UPDATE user_points SET points = points - ? WHERE guild_id = ? AND user_id = ?", (베팅금액, guild_id, user_id))
        conn.commit()
        result_text = f"❌ **틀렸습니다...** (`-{fmt_won(베팅금액)}` 차감)"
        color = discord.Color.red()

    new_pts = get_user_points(guild_id, user_id)
    conn.close()

    embed = discord.Embed(title="🪙 코인토스 결과", description=result_text, color=color)
    embed.add_field(name="내 선택", value=선택, inline=True)
    embed.add_field(name="동전 결과", value=result_coin, inline=True)
    embed.add_field(name="남은 잔액", value=f"💰 `{fmt_won(new_pts)}`", inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

# ---------------------------------------------------------------------------
# 관리자/판매자 및 라이센스 관리 명령어
# ---------------------------------------------------------------------------
@bot.command(name="서버등록")
async def register_guild_cmd(ctx, guild_id_str: str = None, days_str: str = None):
    if not is_admin(ctx):
        return await ctx.send("❌ 이 명령어는 봇 관리자만 사용할 수 있습니다.")
    
    target_guild_id = int(guild_id_str) if guild_id_str else ctx.guild.id
    days = int(days_str) if days_str else 30
    
    expires_at = (datetime.now(KST) + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    
    conn = get_conn()
    conn.execute(
        "INSERT INTO registered_guilds (guild_id, registered_by, registered_at, expires_at) VALUES (?, ?, ?, ?) ON CONFLICT(guild_id) DO UPDATE SET expires_at = ?",
        (target_guild_id, ctx.author.id, now_kst_str(), expires_at, expires_at)
    )
    conn.commit()
    conn.close()
    
    await ctx.send(f"✅ 서버(`{target_guild_id}`)가 성공적으로 등록(연장)되었습니다. 만료일: `{expires_at}`")

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

@bot.tree.command(name="상품등록", description="상점에 새로운 상품을 등록합니다.")
@app_commands.describe(카테고리="상품 카테고리", 상품명="상품 이름", 가격="상품 가격", 재고="재고 개수 (-시 무제한)")
async def register_product(interaction: discord.Interaction, 카테고리: str, 상품명: str, 가격: int, 재고: int = -1):
    if not is_admin_or_seller(interaction):
        return await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
    
    conn = get_conn()
    conn.execute(
        "INSERT INTO prices (guild_id, item, category, price, stock) VALUES (?, ?, ?, ?, ?) ON CONFLICT(guild_id, item) DO UPDATE SET category = ?, price = ?, stock = ?",
        (interaction.guild_id, 상품명, 카테고리, 가격, 재고, 카테고리, 가격, 재고)
    )
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ 상품 **[{카테고리}] {상품명}** (가격: {fmt_won(가격)}, 재고: {재고})이(가) 등록되었습니다.", ephemeral=True)

@bot.tree.command(name="포인트지급", description="유저에게 포인트를 지급합니다.")
@app_commands.describe(유저="지급할 유저", 금액="지급할 포인트")
async def give_points(interaction: discord.Interaction, 유저: discord.Member, 금액: int):
    if not is_admin_or_seller(interaction):
        return await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
    
    conn = get_conn()
    conn.execute(
        "INSERT INTO user_points (guild_id, user_id, points) VALUES (?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE SET points = points + ?",
        (interaction.guild_id, 유저.id, 금액, 금액)
    )
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ {유저.mention}님께 {fmt_won(금액)}을(를) 지급했습니다.", ephemeral=True)

@bot.tree.command(name="포인트조회", description="내 남은 포인트 잔액을 확인합니다.")
async def check_my_points(interaction: discord.Interaction):
    pts = get_user_points(interaction.guild_id, interaction.user.id)
    await interaction.response.send_message(f"💰 내 포인트 잔액: **{fmt_won(pts)}**", ephemeral=True)

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

# ---------------------------------------------------------------------------
# ➕ 새로 추가된 기능 영역 (출석체크 예시)
# ---------------------------------------------------------------------------
@bot.tree.command(name="출석체크", description="매일 출석체크를 하고 포인트를 받으세요!")
async def daily_attendance(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    user_id = interaction.user.id
    today_str = datetime.now(KST).strftime("%Y-%m-%d")

    conn = get_conn()
    row = conn.execute("SELECT last_date FROM attendance WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)).fetchone()
    
    if row and row["last_date"] == today_str:
        conn.close()
        return await interaction.response.send_message("❌ 이미 오늘 출석체크를 완료하셨습니다!", ephemeral=True)

    reward = 500  # 출석 보상 포인트
    conn.execute(
        "INSERT INTO attendance (guild_id, user_id, last_date) VALUES (?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE SET last_date = ?",
        (guild_id, user_id, today_str, today_str)
    )
    conn.execute(
        "INSERT INTO user_points (guild_id, user_id, points) VALUES (?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE SET points = points + ?",
        (guild_id, user_id, reward, reward)
    )
    conn.commit()
    conn.close()

    await interaction.response.send_message(f"✅ 출석체크 완료! 보상으로 **{fmt_won(reward)}**이 지급되었습니다.", ephemeral=True)

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
    bot.add_view(SwordEnhanceView())

    try:
        synced = await bot.tree.sync()
        print(f"슬래시 명령어 {len(synced)}개 동기화 완료")
    except Exception as e:
        print(f"명령어 동기화 실패: {e}")
    print(f"✅ 로그인 완료: {bot.user}")

if __name__ == "__main__":
    bot.run(TOKEN)
