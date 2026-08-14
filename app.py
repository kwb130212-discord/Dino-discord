# -*- coding: utf-8 -*-
import os
import sqlite3
import asyncio
import json
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
            "라이센스등록", "포인트조회", "내구매내역", "출석체크",
            "내정보", "출금신청", "송금하기", "상점목록", "상품검색",
            "건의하기", "출석현황", "버프확인", "인증패널전송", "자판기패널전송",
            "입퇴장로그설정", "상품등록", "포인트지급", "포인트차감", "봇관리자등록",
            "서버관리자등록", "판매자등록", "재고수정", "재고추가", "재고차감",
            "서버정보", "공지발송", "역할지급", "청소하기", "인증역할설정", "서버백업", "서버복구",
            "영수증채널설정", "영수증채널해제"
        ]

        if cmd_name in all_user_commands:
            admin_or_seller_cmds = [
                "인증패널전송", "자판기패널전송", "입퇴장로그설정", "상품등록",
                "포인트지급", "포인트차감", "봇관리자등록", "서버관리자등록",
                "판매자등록", "재고수정", "재고추가", "재고차감", "공지발송",
                "역할지급", "청소하기", "인증역할설정", "서버백업", "서버복구",
                "영수증채널설정", "영수증채널해제"
            ]
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
    conn.execute("PRAGMA foreign_keys = ON")
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
            welcome_channel_id INTEGER,
            log_channel_id INTEGER,
            verify_role_id INTEGER
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
        CREATE TABLE IF NOT EXISTS server_admins (
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
    cur.execute("""
        CREATE TABLE IF NOT EXISTS verify_codes (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            code TEXT NOT NULL,
            PRIMARY KEY (guild_id, user_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS server_backups (
            backup_key TEXT PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            backup_data TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    # [업그레이드] 출금신청/건의사항 내역을 실제로 저장하는 테이블 추가
    cur.execute("""
        CREATE TABLE IF NOT EXISTS withdraw_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            status TEXT DEFAULT '대기중',
            created_at TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
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

def generate_backup_key() -> str:
    chars = string.ascii_uppercase + string.digits
    parts = [''.join(secrets.choice(chars) for _ in range(4)) for _ in range(4)]
    return f"BK-{'-'.join(parts)}"

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

def is_bot_admin(ctx_or_interaction) -> bool:
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

def is_server_admin(ctx_or_interaction) -> bool:
    if is_bot_admin(ctx_or_interaction):
        return True
    if isinstance(ctx_or_interaction, discord.Interaction):
        member = ctx_or_interaction.user
        guild_id = ctx_or_interaction.guild_id
    else:
        member = ctx_or_interaction.author
        guild_id = ctx_or_interaction.guild.id if ctx_or_interaction.guild else None

    if not isinstance(member, discord.Member):
        return False
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM server_admins WHERE guild_id = ? AND user_id = ?", (guild_id, member.id)).fetchone()
    conn.close()
    return row is not None

def is_admin(ctx_or_interaction) -> bool:
    return is_server_admin(ctx_or_interaction)

def can_manage_registration(ctx_or_interaction) -> bool:
    """
    [서버등록/서버해제 전용 권한 체크]
    일반 '관리자 권한(administrator)'만으로는 사용할 수 없고,
    반드시 아래 둘 중 하나여야 합니다.
    1) bot_admins 테이블에 등록된 '봇 관리자'
    2) ADMIN_ROLE_NAME(기본값 '! !디노') 역할 보유자
    (봇 개발자 본인 계정은 항상 허용)
    """
    if isinstance(ctx_or_interaction, discord.Interaction):
        member = ctx_or_interaction.user
        guild_id = ctx_or_interaction.guild_id
    else:
        member = ctx_or_interaction.author
        guild_id = ctx_or_interaction.guild.id if ctx_or_interaction.guild else None

    if not isinstance(member, discord.Member):
        return False

    if any(role.name == ADMIN_ROLE_NAME for role in member.roles):
        return True

    conn = get_conn()
    row = conn.execute("SELECT 1 FROM bot_admins WHERE guild_id = ? AND user_id = ?", (guild_id, member.id)).fetchone()
    conn.close()
    return row is not None

def is_admin_or_seller(ctx_or_interaction) -> bool:
    if is_server_admin(ctx_or_interaction):
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

async def get_log_channel(guild: discord.Guild):
    conn = get_conn()
    row = conn.execute("SELECT log_channel_id FROM guild_settings WHERE guild_id = ?", (guild.id,)).fetchone()
    conn.close()
    if row and row["log_channel_id"]:
        return guild.get_channel(row["log_channel_id"])
    return None

async def send_purchase_receipt(guild: discord.Guild, buyer: discord.abc.User, item_name: str, quantity: int, unit_price: int, total_price: int):
    """
    [신규] 구매 영수증 전송.
    - 서버에 영수증 채널이 설정되어 있으면 → 해당 채널에 전송
    - 설정되어 있지 않으면 → 구매자 개인 DM(갠톡)으로 전송
    반환값: "channel" / "dm" / "failed" (어디로 보냈는지, 혹은 둘 다 실패했는지)
    """
    conn = get_conn()
    row = conn.execute("SELECT receipt_channel_id FROM guild_settings WHERE guild_id = ?", (guild.id,)).fetchone()
    conn.close()

    receipt_embed = discord.Embed(
        title="🧾 구매 영수증",
        color=discord.Color.green(),
        timestamp=datetime.now(KST)
    )
    receipt_embed.set_author(name=str(buyer), icon_url=buyer.display_avatar.url)
    receipt_embed.add_field(name="구매자", value=buyer.mention, inline=True)
    receipt_embed.add_field(name="상품명", value=item_name, inline=True)
    receipt_embed.add_field(name="수량", value=f"{quantity}개", inline=True)
    receipt_embed.add_field(name="개당 가격", value=fmt_won(unit_price), inline=True)
    receipt_embed.add_field(name="총 결제 금액", value=f"**{fmt_won(total_price)}**", inline=True)

    channel_id = row["receipt_channel_id"] if row else None
    channel = guild.get_channel(channel_id) if channel_id else None

    if channel:
        try:
            await channel.send(embed=receipt_embed)
            return "channel"
        except Exception:
            pass  # 채널 전송 실패 시 DM으로 폴백

    try:
        receipt_embed.description = f"**{guild.name}** 서버에서의 구매 영수증입니다."
        await buyer.send(embed=receipt_embed)
        return "dm"
    except Exception:
        return "failed"

# ---------------------------------------------------------------------------
# UI Views & Modals
# ---------------------------------------------------------------------------
class VerifyModal(discord.ui.Modal, title="라이벌 BEST클랜 회원 인증"):
    def __init__(self, target_code: str):
        super().__init__()
        self.target_code = target_code
        self.code_input = discord.ui.TextInput(
            label="4자리 인증 번호 입력",
            placeholder=f"위 안내 메시지에 나온 [{target_code}]를 입력하세요",
            min_length=4,
            max_length=4,
            required=True
        )
        self.add_item(self.code_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            entered_code = self.code_input.value.strip()

            # [정정] DB에 저장된 최신 코드와 비교 (모달 재전송 시 예전 코드로 우회 방지)
            conn = get_conn()
            code_row = conn.execute(
                "SELECT code FROM verify_codes WHERE guild_id = ? AND user_id = ?",
                (interaction.guild.id, interaction.user.id)
            ).fetchone()

            if not code_row or entered_code != code_row["code"]:
                conn.close()
                await interaction.response.send_message(
                    f"❌ 인증 번호가 일치하지 않습니다. `인증하기` 버튼을 다시 눌러 새 번호를 발급받아 주세요.",
                    ephemeral=True
                )
                return

            row = conn.execute("SELECT verify_role_id FROM guild_settings WHERE guild_id = ?", (interaction.guild.id,)).fetchone()

            role_id = row["verify_role_id"] if row else None
            role = interaction.guild.get_role(role_id) if role_id else None

            if role and isinstance(interaction.user, discord.Member):
                if role in interaction.user.roles:
                    conn.close()
                    await interaction.response.send_message("⚠️ 이미 인증이 완료되어 해당 역할을 보유하고 있습니다.", ephemeral=True)
                    return
                await interaction.user.add_roles(role)
                conn.execute("DELETE FROM verify_codes WHERE guild_id = ? AND user_id = ?", (interaction.guild.id, interaction.user.id))
                conn.commit()
                conn.close()
                await interaction.response.send_message(f"✅ 인증이 완료되었습니다! `{role.name}` 역할이 지급되었습니다.", ephemeral=True)
            else:
                conn.close()
                await interaction.response.send_message("❌ 이 서버에 인증 역할이 설정되지 않았거나 찾을 수 없습니다. 관리자에게 문의하세요. (관리자 명령어 `/인증역할설정` 필요)", ephemeral=True)
        except discord.Forbidden:
            if not interaction.response.is_done():
                await interaction.response.send_message("⚠️ 봇의 역할 순위가 낮아 역할을 지급할 수 없습니다. 관리자에게 문의하세요.", ephemeral=True)
        except Exception as e:
            print(f"[인증 오류 발생] {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(f"⚠️ 처리 중 오류가 발생했습니다: {e}", ephemeral=True)

class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="인증하기 🔓", style=discord.ButtonStyle.green, custom_id="verify_button")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        code = "".join(random.choices(string.digits, k=4))
        conn = get_conn()
        conn.execute(
            "INSERT INTO verify_codes (guild_id, user_id, code) VALUES (?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE SET code = ?",
            (interaction.guild.id, interaction.user.id, code, code)
        )
        conn.commit()
        conn.close()
        await interaction.response.send_modal(VerifyModal(target_code=code))

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

            if not items:
                return await inter.response.send_message("❌ 해당 카테고리에 등록된 상품이 없습니다.", ephemeral=True)

            item_view = discord.ui.View(timeout=180)
            item_select = discord.ui.Select(placeholder="구매할 상품을 선택하세요", custom_id="select_buy_item")
            for it in items:
                stock_str = f"재고: {it['stock']}개" if it['stock'] != -1 else "재고: 무제한"
                item_select.add_option(label=it["item"], description=f"가격: {fmt_won(it['price'])} | {stock_str}", value=it["item"])

            async def item_callback(i: discord.Interaction):
                selected_item = item_select.values[0]

                # [정정] 구매 처리를 하나의 커넥션에서 원자적으로 처리 (동시 구매 시 재고 오차 방지)
                c = get_conn()
                try:
                    it_info = c.execute("SELECT price, stock FROM prices WHERE guild_id = ? AND item = ?", (i.guild_id, selected_item)).fetchone()

                    if not it_info:
                        return await i.response.send_message("❌ 존재하지 않는 상품입니다.", ephemeral=True)

                    if it_info["stock"] != -1 and it_info["stock"] <= 0:
                        return await i.response.send_message("❌ 품절된 상품입니다.", ephemeral=True)

                    user_pts = get_user_points(i.guild_id, i.user.id)
                    if user_pts < it_info["price"]:
                        return await i.response.send_message(f"❌ 포인트가 부족합니다! (내 잔액: {fmt_won(user_pts)}, 필요 가격: {fmt_won(it_info['price'])})", ephemeral=True)

                    if it_info["stock"] != -1:
                        # 재고가 음수로 내려가지 않도록 조건부 UPDATE로 재확인
                        cur = c.execute(
                            "UPDATE prices SET stock = stock - 1 WHERE guild_id = ? AND item = ? AND stock > 0",
                            (i.guild_id, selected_item)
                        )
                        if cur.rowcount == 0:
                            c.rollback()
                            return await i.response.send_message("❌ 방금 품절되었습니다. 다시 시도해주세요.", ephemeral=True)

                    c.execute("UPDATE user_points SET points = points - ? WHERE guild_id = ? AND user_id = ?", (it_info["price"], i.guild_id, i.user.id))
                    c.execute(
                        "INSERT INTO transactions (guild_id, buyer_id, buyer_name, item, quantity, unit_price, total_price, memo, created_at, recorded_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (i.guild_id, i.user.id, i.user.display_name, selected_item, 1, it_info["price"], it_info["price"], "자판기 즉시 구매", now_kst_str(), "System")
                    )
                    c.commit()
                finally:
                    c.close()

                # [신규] 영수증 채널이 설정되어 있으면 그 채널로, 없으면 구매자 개인DM(갠톡)으로 자동 전송
                receipt_result = await send_purchase_receipt(
                    guild=i.guild,
                    buyer=i.user,
                    item_name=selected_item,
                    quantity=1,
                    unit_price=it_info["price"],
                    total_price=it_info["price"]
                )

                if receipt_result == "channel":
                    await i.response.send_message("✅ 구매가 완료되었습니다! 영수증이 지정된 채널에 전송되었습니다.", ephemeral=True)
                elif receipt_result == "dm":
                    await i.response.send_message("✅ 구매가 완료되었습니다! 영수증 채널이 설정되지 않아 개인 메시지(DM)로 영수증을 보내드렸습니다.", ephemeral=True)
                else:
                    await i.response.send_message("✅ 구매가 완료되었습니다! (단, DM이 차단되어 있어 영수증 전송에는 실패했습니다. 필요시 `/내구매내역`으로 확인해주세요.)", ephemeral=True)

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

    # [정정] 이미 기간이 남아있는 서버는 남은 기간에 추가로 이어붙임 (기존엔 무조건 지금부터 계산되어 손해)
    now_dt = datetime.now(KST)
    current_reg = conn.execute("SELECT expires_at FROM registered_guilds WHERE guild_id = ?", (interaction.guild_id,)).fetchone()
    if current_reg and current_reg["expires_at"]:
        cur_exp = datetime.strptime(current_reg["expires_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
        start_dt = max(now_dt, cur_exp)
    else:
        start_dt = now_dt
    expires_at = (start_dt + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    conn.execute("UPDATE licenses SET is_used = 1, used_by_guild = ?, used_at = ? WHERE license_key = ?", (interaction.guild_id, now_kst_str(), 라이센스키))
    conn.execute(
        "INSERT INTO registered_guilds (guild_id, registered_by, registered_at, expires_at) VALUES (?, ?, ?, ?) ON CONFLICT(guild_id) DO UPDATE SET expires_at = ?",
        (interaction.guild_id, interaction.user.id, now_kst_str(), expires_at, expires_at)
    )
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"🎉 라이센스 등록이 완료되었습니다! (이용 기간: {days}일, 만료일: {expires_at})", ephemeral=True)

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
    conn = get_conn()
    tx_count = conn.execute("SELECT COUNT(*) as cnt FROM transactions WHERE guild_id = ? AND buyer_id = ?", (interaction.guild_id, interaction.user.id)).fetchone()["cnt"]
    att_row = conn.execute("SELECT last_date FROM attendance WHERE guild_id = ? AND user_id = ?", (interaction.guild_id, interaction.user.id)).fetchone()
    conn.close()
    embed = discord.Embed(title=f"👤 {interaction.user.display_name}님의 프로필", color=discord.Color.blue())
    embed.set_thumbnail(url=interaction.user.display_avatar.url)
    embed.add_field(name="💰 보유 포인트", value=fmt_won(pts), inline=True)
    embed.add_field(name="🛒 누적 구매 횟수", value=f"{tx_count}회", inline=True)
    embed.add_field(name="📅 최근 출석일", value=att_row["last_date"] if att_row else "기록 없음", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="출금신청", description="보유 포인트를 현금 환전/출금 신청합니다.")
@app_commands.describe(금액="출금할 포인트")
async def withdraw_points(interaction: discord.Interaction, 금액: int):
    if 금액 <= 0:
        return await interaction.response.send_message("❌ 올바른 금액을 입력하세요.", ephemeral=True)
    pts = get_user_points(interaction.guild_id, interaction.user.id)
    if pts < 금액:
        return await interaction.response.send_message("❌ 포인트가 부족합니다.", ephemeral=True)

    # [업그레이드] 신청 내역을 실제로 DB에 저장 + 로그 채널로 통지
    conn = get_conn()
    conn.execute(
        "INSERT INTO withdraw_requests (guild_id, user_id, amount, status, created_at) VALUES (?, ?, ?, '대기중', ?)",
        (interaction.guild_id, interaction.user.id, 금액, now_kst_str())
    )
    conn.commit()
    conn.close()

    await interaction.response.send_message(f"✅ {fmt_won(금액)} 출금 신청이 관리자에게 접수되었습니다.", ephemeral=True)

    log_ch = await get_log_channel(interaction.guild)
    if log_ch:
        embed = discord.Embed(title="💸 출금 신청 접수", color=discord.Color.orange(), timestamp=datetime.now(KST))
        embed.add_field(name="신청자", value=interaction.user.mention, inline=True)
        embed.add_field(name="신청 금액", value=fmt_won(금액), inline=True)
        await log_ch.send(embed=embed)

@bot.tree.command(name="송금하기", description="다른 유저에게 포인트를 선물합니다.")
@app_commands.describe(유저="선물할 유저", 금액="선물할 포인트")
async def send_points(interaction: discord.Interaction, 유저: discord.Member, 금액: int):
    if 금액 <= 0:
        return await interaction.response.send_message("❌ 1 이상의 금액을 입력하세요.", ephemeral=True)
    if 유저.id == interaction.user.id:
        return await interaction.response.send_message("❌ 자기 자신에게는 송금할 수 없습니다.", ephemeral=True)
    if 유저.bot:
        return await interaction.response.send_message("❌ 봇에게는 송금할 수 없습니다.", ephemeral=True)
    pts = get_user_points(interaction.guild_id, interaction.user.id)
    if pts < 금액:
        return await interaction.response.send_message("❌ 포인트가 부족합니다.", ephemeral=True)
    conn = get_conn()
    conn.execute("UPDATE user_points SET points = points - ? WHERE guild_id = ? AND user_id = ?", (금액, interaction.guild_id, interaction.user.id))
    conn.execute("INSERT INTO user_points (guild_id, user_id, points) VALUES (?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE SET points = points + ?", (interaction.guild_id, 유저.id, 금액, 금액))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ {유저.mention}님께 {fmt_won(금액)}을(를) 송금했습니다.", ephemeral=True)
    try:
        await 유저.send(f"💌 {interaction.guild.name} 서버에서 **{interaction.user.display_name}**님이 {fmt_won(금액)}을(를) 보내주셨습니다!")
    except Exception:
        pass

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
    # [업그레이드] 실제로 저장 + 로그 채널로 전달 (기존엔 그냥 사라짐)
    conn = get_conn()
    conn.execute(
        "INSERT INTO suggestions (guild_id, user_id, content, created_at) VALUES (?, ?, ?, ?)",
        (interaction.guild_id, interaction.user.id, 내용, now_kst_str())
    )
    conn.commit()
    conn.close()

    await interaction.response.send_message("✅ 건의사항이 운영진에게 전송되었습니다.", ephemeral=True)

    log_ch = await get_log_channel(interaction.guild)
    if log_ch:
        embed = discord.Embed(title="💡 새로운 건의사항", description=내용, color=discord.Color.gold(), timestamp=datetime.now(KST))
        embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
        await log_ch.send(embed=embed)

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

# 패널 전송 명령어들
@bot.tree.command(name="인증패널전송", description="회원 인증 패널을 현재 채널에 전송합니다.")
async def send_verify_panel(interaction: discord.Interaction):
    if not is_server_admin(interaction):
        return await interaction.response.send_message("❌ 서버 관리자만 사용할 수 있습니다.", ephemeral=True)

    embed = discord.Embed(
        title="🔒 라이벌 BEST클랜 회원 인증",
        description="아래 [인증하기 🔓] 버튼을 누른 후, 화면에 안내되는 4자리 숫자를 입력해 주세요.",
        color=discord.Color.from_rgb(46, 204, 113)
    )
    await interaction.channel.send(embed=embed, view=VerifyView())
    await interaction.response.send_message("✅ 인증 패널을 전송했습니다.", ephemeral=True)

@bot.tree.command(name="자판기패널전송", description="자판기(상점) 패널을 현재 채널에 전송합니다.")
async def send_vending_panel(interaction: discord.Interaction):
    if not is_server_admin(interaction):
        return await interaction.response.send_message("❌ 서버 관리자만 사용할 수 있습니다.", ephemeral=True)

    embed = discord.Embed(
        title="🛒 서버 전용 자판기",
        description="아래 버튼을 눌러 상품을 구매하거나 포인트를 확인하세요.",
        color=discord.Color.from_rgb(52, 152, 219)
    )
    await interaction.channel.send(embed=embed, view=MainVendingView())
    await interaction.response.send_message("✅ 자판기 패널을 전송했습니다.", ephemeral=True)

@bot.tree.command(name="인증역할설정", description="인증 완료 시 지급할 역할을 설정합니다.")
@app_commands.describe(역할="지급할 디스코드 역할")
async def set_verify_role(interaction: discord.Interaction, 역할: discord.Role):
    if not is_server_admin(interaction):
        return await interaction.response.send_message("❌ 서버 관리자만 사용할 수 있습니다.", ephemeral=True)

    # [정정] 봇보다 높은 역할을 지정하면 나중에 지급이 실패하므로 미리 경고
    if interaction.guild.me.top_role <= 역할:
        await interaction.response.send_message(
            f"⚠️ **경고:** `{역할.name}` 역할이 봇의 최상위 역할보다 높거나 같습니다.\n"
            "이 상태로는 실제 인증 시 역할 지급이 실패할 수 있습니다. 서버 설정에서 봇 역할을 더 위로 올려주세요.\n"
            "(설정은 그대로 저장되었습니다.)",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(f"✅ 인증 완료 시 지급될 역할이 **{역할.name}**(으)로 설정되었습니다.", ephemeral=True)

    conn = get_conn()
    conn.execute(
        "INSERT INTO guild_settings (guild_id, verify_role_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET verify_role_id = ?",
        (interaction.guild.id, 역할.id, 역할.id)
    )
    conn.commit()
    conn.close()

@bot.tree.command(name="서버백업", description="현재 서버의 상점, 재고, 설정 데이터를 백업하고 백업 키를 발급합니다.")
async def create_server_backup(interaction: discord.Interaction):
    if not is_server_admin(interaction):
        return await interaction.response.send_message("❌ 서버 관리자만 사용할 수 있습니다.", ephemeral=True)

    conn = get_conn()
    guild_id = interaction.guild_id

    prices = [dict(row) for row in conn.execute("SELECT * FROM prices WHERE guild_id = ?", (guild_id,)).fetchall()]
    item_stocks = [dict(row) for row in conn.execute("SELECT * FROM item_stocks WHERE guild_id = ?", (guild_id,)).fetchall()]
    permanent_stocks = [dict(row) for row in conn.execute("SELECT * FROM permanent_stocks WHERE guild_id = ?", (guild_id,)).fetchall()]
    settings = conn.execute("SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,)).fetchone()
    settings_dict = dict(settings) if settings else {}
    conn.close()

    backup_payload = {
        "prices": prices,
        "item_stocks": item_stocks,
        "permanent_stocks": permanent_stocks,
        "settings": settings_dict
    }

    backup_json = json.dumps(backup_payload, ensure_ascii=False)
    backup_key = generate_backup_key()

    conn = get_conn()
    conn.execute(
        "INSERT INTO server_backups (backup_key, guild_id, backup_data, created_at) VALUES (?, ?, ?, ?)",
        (backup_key, guild_id, backup_json, now_kst_str())
    )
    conn.commit()
    conn.close()

    embed = discord.Embed(title="💾 서버 백업 완료", color=discord.Color.green())
    embed.add_field(name="발급된 백업 키", value=f"`{backup_key}`", inline=False)
    embed.description = "⚠️ **주의:** 이 백업 키는 타인에게 노출되지 않도록 안전한 곳에 보관해 주세요."
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="서버복구", description="발급받은 백업 키를 이용해 서버 데이터를 복구합니다.")
@app_commands.describe(백업키="복구에 사용할 백업 키")
async def restore_server_backup(interaction: discord.Interaction, 백업키: str):
    if not is_server_admin(interaction):
        return await interaction.response.send_message("❌ 서버 관리자만 사용할 수 있습니다.", ephemeral=True)

    conn = get_conn()
    row = conn.execute("SELECT backup_data FROM server_backups WHERE backup_key = ?", (백업키,)).fetchone()
    if not row:
        conn.close()
        return await interaction.response.send_message("❌ 유효하지 않거나 존재하지 않는 백업 키입니다.", ephemeral=True)

    try:
        backup_payload = json.loads(row["backup_data"])
    except Exception as e:
        conn.close()
        return await interaction.response.send_message(f"❌ 백업 데이터 파싱 중 오류가 발생했습니다: {e}", ephemeral=True)

    guild_id = interaction.guild_id

    conn.execute("DELETE FROM prices WHERE guild_id = ?", (guild_id,))
    conn.execute("DELETE FROM item_stocks WHERE guild_id = ?", (guild_id,))
    conn.execute("DELETE FROM permanent_stocks WHERE guild_id = ?", (guild_id,))

    for p in backup_payload.get("prices", []):
        conn.execute(
            "INSERT INTO prices (guild_id, item, category, price, stock, target_type, is_permanent, role_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (guild_id, p.get("item"), p.get("category", "기타"), p.get("price", 0), p.get("stock", -1), p.get("target_type", "standard"), p.get("is_permanent", 0), p.get("role_id"))
        )

    for istock in backup_payload.get("item_stocks", []):
        conn.execute(
            "INSERT INTO item_stocks (guild_id, item, content, is_used) VALUES (?, ?, ?, ?)",
            (guild_id, istock.get("item"), istock.get("content"), istock.get("is_used", 0))
        )

    for pstock in backup_payload.get("permanent_stocks", []):
        conn.execute(
            "INSERT INTO permanent_stocks (guild_id, item, content) VALUES (?, ?, ?)",
            (guild_id, pstock.get("item"), pstock.get("content"))
        )

    s = backup_payload.get("settings", {})
    if s:
        conn.execute(
            """INSERT INTO guild_settings (guild_id, receipt_channel_id, welcome_channel_id, log_channel_id, verify_role_id)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(guild_id) DO UPDATE SET
               receipt_channel_id = ?, welcome_channel_id = ?, log_channel_id = ?, verify_role_id = ?""",
            (guild_id, s.get("receipt_channel_id"), s.get("welcome_channel_id"), s.get("log_channel_id"), s.get("verify_role_id"),
             s.get("receipt_channel_id"), s.get("welcome_channel_id"), s.get("log_channel_id"), s.get("verify_role_id"))
        )

    conn.commit()
    conn.close()

    await interaction.response.send_message("✅ 백업 키를 통해 서버 데이터가 성공적으로 복구되었습니다!", ephemeral=True)

@bot.tree.command(name="영수증채널설정", description="구매 영수증이 출력될 채널을 지정합니다. (설정 안 하면 구매자 개인DM으로 발송)")
@app_commands.describe(채널="영수증을 전송할 텍스트 채널")
async def set_receipt_channel(interaction: discord.Interaction, 채널: discord.TextChannel):
    if not is_server_admin(interaction):
        return await interaction.response.send_message("❌ 서버 관리자만 사용할 수 있습니다.", ephemeral=True)

    conn = get_conn()
    conn.execute(
        "INSERT INTO guild_settings (guild_id, receipt_channel_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET receipt_channel_id = ?",
        (interaction.guild_id, 채널.id, 채널.id)
    )
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ 구매 영수증이 {채널.mention} 채널로 전송되도록 설정되었습니다.", ephemeral=True)

@bot.tree.command(name="영수증채널해제", description="영수증 채널 설정을 해제합니다. (해제 시 다시 구매자 개인DM으로 발송)")
async def unset_receipt_channel(interaction: discord.Interaction):
    if not is_server_admin(interaction):
        return await interaction.response.send_message("❌ 서버 관리자만 사용할 수 있습니다.", ephemeral=True)

    conn = get_conn()
    conn.execute(
        "INSERT INTO guild_settings (guild_id, receipt_channel_id) VALUES (?, NULL) ON CONFLICT(guild_id) DO UPDATE SET receipt_channel_id = NULL",
        (interaction.guild_id,)
    )
    conn.commit()
    conn.close()
    await interaction.response.send_message("✅ 영수증 채널 설정이 해제되었습니다. 앞으로 구매자 개인DM(갠톡)으로 영수증이 발송됩니다.", ephemeral=True)

@bot.tree.command(name="입퇴장로그설정", description="입퇴장 로그(강퇴/밴 포함)를 출력할 채널을 설정합니다.")
@app_commands.describe(채널="로그를 출력할 텍스트 채널")
async def set_log_channel(interaction: discord.Interaction, 채널: discord.TextChannel):
    if not is_server_admin(interaction):
        return await interaction.response.send_message("❌ 서버 관리자만 사용할 수 있습니다.", ephemeral=True)

    conn = get_conn()
    conn.execute("INSERT INTO guild_settings (guild_id, log_channel_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET log_channel_id = ?", (interaction.guild_id, 채널.id, 채널.id))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ 입퇴장 로그 채널이 {채널.mention}로 설정되었습니다.", ephemeral=True)

@bot.tree.command(name="상품등록", description="상점에 새로운 상품을 등록합니다.")
@app_commands.describe(카테고리="카테고리", 상품명="상품명", 가격="가격", 재고="재고 (-1은 무제한)")
async def register_product(interaction: discord.Interaction, 카테고리: str, 상품명: str, 가격: int, 재고: int = -1):
    if not is_admin_or_seller(interaction):
        return await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
    if 가격 < 0:
        return await interaction.response.send_message("❌ 가격은 0 이상이어야 합니다.", ephemeral=True)
    conn = get_conn()
    conn.execute("INSERT INTO prices (guild_id, item, category, price, stock) VALUES (?, ?, ?, ?, ?) ON CONFLICT(guild_id, item) DO UPDATE SET category = ?, price = ?, stock = ?", (interaction.guild_id, 상품명, 카테고리, 가격, 재고, 카테고리, 가격, 재고))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ 상품 **[{카테고리}] {상품명}** 등록 완료!", ephemeral=True)

# 실시간 재고 추가/차감 명령어
@bot.tree.command(name="재고추가", description="특정 상품의 재고를 실시간으로 늘립니다.")
@app_commands.describe(상품명="상품명", 수량="추가할 수량")
async def add_stock(interaction: discord.Interaction, 상품명: str, 수량: int):
    if not is_admin_or_seller(interaction):
        return await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
    if 수량 <= 0:
        return await interaction.response.send_message("❌ 추가할 수량은 1 이상이어야 합니다.", ephemeral=True)

    conn = get_conn()
    item = conn.execute("SELECT stock FROM prices WHERE guild_id = ? AND item = ?", (interaction.guild_id, 상품명)).fetchone()
    if not item:
        conn.close()
        return await interaction.response.send_message(f"❌ '{상품명}' 상품을 찾을 수 없습니다.", ephemeral=True)

    current_stock = item["stock"]
    if current_stock == -1:
        conn.close()
        return await interaction.response.send_message(f"⚠️ '{상품명}' 상품은 재고가 무제한(-1)으로 설정되어 있어 수량을 변경할 수 없습니다.", ephemeral=True)

    new_stock = current_stock + 수량
    conn.execute("UPDATE prices SET stock = ? WHERE guild_id = ? AND item = ?", (new_stock, interaction.guild_id, 상품명))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ **{상품명}** 재고가 **{수량}개** 추가되었습니다. (현재 재고: {new_stock}개)", ephemeral=True)

@bot.tree.command(name="재고차감", description="특정 상품의 재고를 실시간으로 줄입니다.")
@app_commands.describe(상품명="상품명", 수량="차감할 수량")
async def sub_stock(interaction: discord.Interaction, 상품명: str, 수량: int):
    if not is_admin_or_seller(interaction):
        return await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
    if 수량 <= 0:
        return await interaction.response.send_message("❌ 차감할 수량은 1 이상이어야 합니다.", ephemeral=True)

    conn = get_conn()
    item = conn.execute("SELECT stock FROM prices WHERE guild_id = ? AND item = ?", (interaction.guild_id, 상품명)).fetchone()
    if not item:
        conn.close()
        return await interaction.response.send_message(f"❌ '{상품명}' 상품을 찾을 수 없습니다.", ephemeral=True)

    current_stock = item["stock"]
    if current_stock == -1:
        conn.close()
        return await interaction.response.send_message(f"⚠️ '{상품명}' 상품은 재고가 무제한(-1)으로 설정되어 있습니다.", ephemeral=True)

    new_stock = max(0, current_stock - 수량)
    conn.execute("UPDATE prices SET stock = ? WHERE guild_id = ? AND item = ?", (new_stock, interaction.guild_id, 상품명))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ **{상품명}** 재고가 **{수량}개** 차감되었습니다. (현재 재고: {new_stock}개)", ephemeral=True)

@bot.tree.command(name="포인트지급", description="유저에게 포인트를 지급합니다.")
@app_commands.describe(유저="대상 유저", 금액="지급할 포인트")
async def give_points(interaction: discord.Interaction, 유저: discord.Member, 금액: int):
    if not is_admin_or_seller(interaction):
        return await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
    if 금액 <= 0:
        return await interaction.response.send_message("❌ 1 이상의 금액을 입력하세요.", ephemeral=True)
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
    if 금액 <= 0:
        return await interaction.response.send_message("❌ 1 이상의 금액을 입력하세요.", ephemeral=True)
    conn = get_conn()
    conn.execute("UPDATE user_points SET points = MAX(0, points - ?) WHERE guild_id = ? AND user_id = ?", (금액, interaction.guild_id, 유저.id))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ {유저.mention}님의 포인트 {fmt_won(금액)} 차감 완료!", ephemeral=True)

@bot.tree.command(name="봇관리자등록", description="새로운 봇 관리자를 임명합니다.")
@app_commands.describe(유저="임명할 유저")
async def add_bot_admin(interaction: discord.Interaction, 유저: discord.Member):
    if not is_bot_admin(interaction):
        return await interaction.response.send_message("❌ 봇 관리자만 가능합니다.", ephemeral=True)
    conn = get_conn()
    conn.execute("INSERT INTO bot_admins (guild_id, user_id, added_by, added_at) VALUES (?, ?, ?, ?) ON CONFLICT(guild_id, user_id) DO NOTHING", (interaction.guild_id, 유저.id, interaction.user.id, now_kst_str()))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ {유저.mention}님을 봇 관리자로 등록했습니다.", ephemeral=True)

@bot.tree.command(name="서버관리자등록", description="새로운 서버 관리자를 임명합니다.")
@app_commands.describe(유저="임명할 유저")
async def add_server_admin(interaction: discord.Interaction, 유저: discord.Member):
    if not is_bot_admin(interaction):
        return await interaction.response.send_message("❌ 봇 관리자만 서버 관리자를 등록할 수 있습니다.", ephemeral=True)
    conn = get_conn()
    conn.execute("INSERT INTO server_admins (guild_id, user_id, added_by, added_at) VALUES (?, ?, ?, ?) ON CONFLICT(guild_id, user_id) DO NOTHING", (interaction.guild_id, 유저.id, interaction.user.id, now_kst_str()))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ {유저.mention}님을 서버 관리자로 등록했습니다.", ephemeral=True)

@bot.tree.command(name="판매자등록", description="새로운 판매자를 임명합니다.")
@app_commands.describe(유저="임명할 유저")
async def register_seller_cmd(interaction: discord.Interaction, 유저: discord.Member):
    if not is_server_admin(interaction):
        return await interaction.response.send_message("❌ 서버 관리자만 가능합니다.", ephemeral=True)
    conn = get_conn()
    conn.execute("INSERT INTO bot_sellers (guild_id, user_id, added_by, added_at) VALUES (?, ?, ?, ?) ON CONFLICT(guild_id, user_id) DO NOTHING", (interaction.guild_id, 유저.id, interaction.user.id, now_kst_str()))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ {유저.mention}님을 판매자로 등록했습니다.", ephemeral=True)

@bot.tree.command(name="재고수정", description="등록된 상품의 재고를 특정 숫자로 강제 수정합니다.")
@app_commands.describe(상품명="상품명", 재고="변경할 재고 개수")
async def update_stock(interaction: discord.Interaction, 상품명: str, 재고: int):
    if not is_admin_or_seller(interaction):
        return await interaction.response.send_message("❌ 권한이 없습니다.", ephemeral=True)
    conn = get_conn()
    item = conn.execute("SELECT 1 FROM prices WHERE guild_id = ? AND item = ?", (interaction.guild_id, 상품명)).fetchone()
    if not item:
        conn.close()
        return await interaction.response.send_message(f"❌ '{상품명}' 상품을 찾을 수 없습니다.", ephemeral=True)
    conn.execute("UPDATE prices SET stock = ? WHERE guild_id = ? AND item = ?", (재고, interaction.guild_id, 상품명))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ '{상품명}' 상품의 재고를 {재고}개로 수정했습니다.", ephemeral=True)

@bot.tree.command(name="서버정보", description="현재 서버의 기본 정보와 봇 등록 상태를 확인합니다.")
async def server_info(interaction: discord.Interaction):
    is_reg = is_guild_registered(interaction.guild_id)
    conn = get_conn()
    exp_row = conn.execute("SELECT expires_at FROM registered_guilds WHERE guild_id = ?", (interaction.guild_id,)).fetchone()
    conn.close()
    embed = discord.Embed(title=f"📊 {interaction.guild.name} 서버 정보", color=discord.Color.blue())
    embed.add_field(name="서버 ID", value=str(interaction.guild_id), inline=True)
    embed.add_field(name="라이센스 승인", value="✅ 승인됨" if is_reg else "❌ 미승인", inline=True)
    if exp_row and exp_row["expires_at"]:
        embed.add_field(name="만료일", value=exp_row["expires_at"], inline=True)
    embed.add_field(name="멤버 수", value=str(interaction.guild.member_count), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="공지발송", description="서버 전체에 공지사항을 임베드로 전송합니다.")
@app_commands.describe(내용="공지 내용")
async def send_announcement(interaction: discord.Interaction, 내용: str):
    if not is_server_admin(interaction):
        return await interaction.response.send_message("❌ 서버 관리자만 가능합니다.", ephemeral=True)
    embed = discord.Embed(title="📢 서버 공지사항", description=내용, color=discord.Color.red(), timestamp=datetime.now(KST))
    embed.set_footer(text=f"작성자: {interaction.user.display_name}")
    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("✅ 공지 전송 완료", ephemeral=True)

@bot.tree.command(name="역할지급", description="유저에게 특정 역할을 부여합니다.")
@app_commands.describe(유저="대상 유저", 역할="부여할 역할")
async def give_role(interaction: discord.Interaction, 유저: discord.Member, 역할: discord.Role):
    if not is_server_admin(interaction):
        return await interaction.response.send_message("❌ 서버 관리자만 가능합니다.", ephemeral=True)
    if interaction.guild.me.top_role <= 역할:
        return await interaction.response.send_message(f"❌ 봇의 역할 순위가 `{역할.name}`보다 낮아 지급할 수 없습니다. 봇 역할을 더 위로 올려주세요.", ephemeral=True)
    try:
        await 유저.add_roles(역할)
        await interaction.response.send_message(f"✅ {유저.mention}님께 {역할.name} 역할을 지급했습니다.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ 권한 부족으로 역할을 지급하지 못했습니다.", ephemeral=True)

@bot.tree.command(name="청소하기", description="채팅 메시지를 지정한 개수만큼 삭제합니다.")
@app_commands.describe(개수="삭제할 메시지 개수 (최대 100)")
async def clear_messages(interaction: discord.Interaction, 개수: int):
    if not is_server_admin(interaction):
        return await interaction.response.send_message("❌ 서버 관리자만 가능합니다.", ephemeral=True)
    if 개수 <= 0:
        return await interaction.response.send_message("❌ 1 이상의 개수를 입력하세요.", ephemeral=True)
    개수 = min(개수, 100)  # [정정] 디스코드 API 제한(최대 100개)을 넘지 않도록 방어
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=개수)
    await interaction.followup.send(f"✅ 메시지 {len(deleted)}개를 삭제했습니다.", ephemeral=True)

# ---------------------------------------------------------------------------
# 관리자 전용 퇴장 유저 밴/강퇴 기능 (로그 채널 내부 버튼 연동)
# ---------------------------------------------------------------------------
class LogAdminActionView(discord.ui.View):
    def __init__(self, target_user_id: int):
        super().__init__(timeout=None)
        self.target_user_id = target_user_id

        kick_btn = discord.ui.Button(label="강퇴 (Kick)", style=discord.ButtonStyle.secondary, custom_id=f"mod_kick_{target_user_id}")
        kick_btn.callback = self.kick_callback
        self.add_item(kick_btn)

        ban_btn = discord.ui.Button(label="밴 (Ban)", style=discord.ButtonStyle.danger, custom_id=f"mod_ban_{target_user_id}")
        ban_btn.callback = self.ban_callback
        self.add_item(ban_btn)

    async def kick_callback(self, interaction: discord.Interaction):
        if not is_server_admin(interaction):
            return await interaction.response.send_message("❌ 서버 관리자만 사용할 수 있습니다.", ephemeral=True)

        target = interaction.guild.get_member(self.target_user_id)
        if not target:
            try:
                target = await interaction.guild.fetch_member(self.target_user_id)
            except Exception:
                pass

        if not target:
            return await interaction.response.send_message("❌ 이미 서버를 완전히 이탈했거나 찾을 수 없는 유저입니다.", ephemeral=True)

        try:
            await interaction.guild.kick(target, reason=f"관리자({interaction.user})에 의한 강퇴")
            await interaction.response.send_message("✅ 성공적으로 해당 유저를 강퇴했습니다.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ 봇의 권한이 부족하여 강퇴할 수 없습니다.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ 강퇴 실패: {e}", ephemeral=True)

    async def ban_callback(self, interaction: discord.Interaction):
        if not is_server_admin(interaction):
            return await interaction.response.send_message("❌ 서버 관리자만 사용할 수 있습니다.", ephemeral=True)

        try:
            user = await bot.fetch_user(self.target_user_id)
            await interaction.guild.ban(user, reason=f"관리자({interaction.user})에 의한 밴")
            await interaction.response.send_message("✅ 성공적으로 해당 유저를 차단(밴)했습니다.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ 봇의 권한이 부족하여 밴할 수 없습니다.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ 밴 실패: {e}", ephemeral=True)

# ---------------------------------------------------------------------------
# 전통적 접두사 명령어 및 이벤트 리스너 (입퇴장 로그 포함)
# ---------------------------------------------------------------------------
@bot.command(name="서버등록")
async def register_guild_cmd(ctx, guild_id_str: str = None, days_str: str = None):
    # [정정] '관리자 권한'만으로는 사용 불가. 봇관리자(bot_admins) 또는 '! !디노' 역할 보유자 + 봇 개발자만 가능
    if not (await bot.is_owner(ctx.author) or can_manage_registration(ctx)):
        return await ctx.send(f"❌ 이 명령어는 봇 관리자 또는 `{ADMIN_ROLE_NAME}` 역할 보유자만 사용할 수 있습니다.")

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
    await ctx.send(f"✅ 서버(`{target_guild_id}`) 등록 완료. 만료일: `{expires_at}`")

@bot.command(name="서버해제")
async def unregister_guild_cmd(ctx, guild_id_str: str = None):
    # [신규] 서버등록과 동일한 권한 체계 (봇관리자 또는 '! !디노' 역할 보유자 + 봇 개발자만 가능)
    if not (await bot.is_owner(ctx.author) or can_manage_registration(ctx)):
        return await ctx.send(f"❌ 이 명령어는 봇 관리자 또는 `{ADMIN_ROLE_NAME}` 역할 보유자만 사용할 수 있습니다.")

    target_guild_id = int(guild_id_str) if guild_id_str else ctx.guild.id
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM registered_guilds WHERE guild_id = ?", (target_guild_id,)).fetchone()
    if not row:
        conn.close()
        return await ctx.send(f"⚠️ 서버(`{target_guild_id}`)는 애초에 등록되어 있지 않습니다.")

    conn.execute("DELETE FROM registered_guilds WHERE guild_id = ?", (target_guild_id,))
    conn.commit()
    conn.close()
    await ctx.send(f"✅ 서버(`{target_guild_id}`)의 등록(라이센스)이 해제되었습니다.")

@bot.command(name="강제동기화")
async def force_resync(ctx: commands.Context):
    """[봇 관리자 전용] 옛날 슬래시 명령어 캐시를 지우고 현재 코드 기준으로 재등록합니다."""
    if not is_bot_admin(ctx):
        return await ctx.send("❌ 이 명령어는 봇 관리자만 사용할 수 있습니다.")

    msg = await ctx.send("🔄 명령어 초기화 및 재동기화 중...")
    bot.tree.clear_commands(guild=None)
    await bot.tree.sync()

    if ctx.guild:
        bot.tree.copy_global_to(guild=ctx.guild)
        synced_guild = await bot.tree.sync(guild=ctx.guild)
        await msg.edit(content=f"✅ 재동기화 완료! (이 서버 기준 {len(synced_guild)}개 명령어 즉시 반영)\n"
                                f"디스코드 앱을 재시작하면 목록이 깔끔하게 갱신됩니다.")
    else:
        await msg.edit(content="✅ 글로벌 재동기화 완료! (전파까지 최대 1시간 소요될 수 있습니다)")

@bot.event
async def on_member_join(member: discord.Member):
    ch = await get_log_channel(member.guild)
    if ch:
        embed = discord.Embed(title="📥 유저 입장", description=f"{member.mention} (`{member}`) 님이 서버에 입장하셨습니다.", color=discord.Color.green(), timestamp=datetime.now(KST))
        embed.set_thumbnail(url=member.display_avatar.url)
        await ch.send(embed=embed)

@bot.event
async def on_member_remove(member: discord.Member):
    ch = await get_log_channel(member.guild)
    if not ch:
        return

    exit_type = "퇴장 (서버 이탈)"
    try:
        # [정정] 오타(<업그레 10) 수정 완료 -> 정상적으로 최근 10초 이내 감사로그만 확인
        async for entry in member.guild.audit_logs(limit=5, action=discord.AuditLogAction.kick):
            if entry.target.id == member.id and (datetime.now(timezone.utc) - entry.created_at).total_seconds() < 10:
                exit_type = f"강퇴됨 (담당자: {entry.user})"
                break
        async for entry in member.guild.audit_logs(limit=5, action=discord.AuditLogAction.ban):
            if entry.target.id == member.id and (datetime.now(timezone.utc) - entry.created_at).total_seconds() < 10:
                exit_type = f"밴(차단)됨 (담당자: {entry.user})"
                break
    except discord.Forbidden:
        pass  # 감사로그 조회 권한이 없는 경우 조용히 무시
    except Exception:
        pass

    embed = discord.Embed(title="📤 유저 퇴장", description=f"{member.mention} (`{member}`) 님이 서버를 나가셨습니다.\n**유형:** {exit_type}", color=discord.Color.red(), timestamp=datetime.now(KST))
    view = LogAdminActionView(target_user_id=member.id)
    await ch.send(embed=embed, view=view)

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
    if not TOKEN or TOKEN == "YOUR_BOT_TOKEN_HERE":
        raise SystemExit("❌ DISCORD_TOKEN이 설정되지 않았습니다. .env 환경변수를 설정하거나 토큰을 입력하세요.")
    bot.run(TOKEN)
