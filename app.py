# -*- coding: utf-8 -*-
import os
import sqlite3
import json
import secrets
import string
import random
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

# ==============================================================================
# 1. 환경변수 및 기본 설정 (.env 연동 완료)
# ==============================================================================
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")

ADMIN_ROLE_NAME = os.getenv("ADMIN_ROLE_NAME", "! !디노")
DB_PATH = os.getenv("DB_PATH", "shop.db")
KST = timezone(timedelta(hours=9))

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

def now_kst_str() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

def fmt_won(n: int) -> str:
    return f"{n:,}원"

# ==============================================================================
# 2. 데이터베이스 매니저 (추상화 클래스)
# ==============================================================================
class DB:
    """반복되는 DB 연결/해제/커밋을 제거하기 위한 정적 매니저 클래스"""
    @staticmethod
    def get_connection():
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def fetchone(query: str, *params) -> Optional[sqlite3.Row]:
        with DB.get_connection() as conn:
            return conn.execute(query, params).fetchone()

    @staticmethod
    def fetchall(query: str, *params) -> list[sqlite3.Row]:
        with DB.get_connection() as conn:
            return conn.execute(query, params).fetchall()

    @staticmethod
    def execute(query: str, *params) -> int:
        with DB.get_connection() as conn:
            cur = conn.execute(query, params)
            conn.commit()
            return cur.rowcount

    @staticmethod
    def init_db():
        queries = [
            """CREATE TABLE IF NOT EXISTS prices (
                guild_id INTEGER NOT NULL, item TEXT NOT NULL, category TEXT DEFAULT '기타',
                price INTEGER NOT NULL DEFAULT 0, stock INTEGER DEFAULT -1, target_type TEXT DEFAULT 'standard',
                is_permanent INTEGER DEFAULT 0, role_id INTEGER DEFAULT NULL, PRIMARY KEY (guild_id, item)
            )""",
            "CREATE TABLE IF NOT EXISTS item_stocks (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, item TEXT NOT NULL, content TEXT NOT NULL, is_used INTEGER DEFAULT 0)",
            "CREATE TABLE IF NOT EXISTS permanent_stocks (guild_id INTEGER NOT NULL, item TEXT NOT NULL, content TEXT NOT NULL, PRIMARY KEY (guild_id, item))",
            "CREATE TABLE IF NOT EXISTS user_points (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, points INTEGER DEFAULT 0, PRIMARY KEY (guild_id, user_id))",
            """CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, buyer_id INTEGER NOT NULL,
                buyer_name TEXT NOT NULL, item TEXT NOT NULL, quantity INTEGER NOT NULL, unit_price INTEGER NOT NULL,
                total_price INTEGER NOT NULL, memo TEXT, created_at TEXT NOT NULL, recorded_by TEXT NOT NULL
            )""",
            "CREATE TABLE IF NOT EXISTS registered_guilds (guild_id INTEGER PRIMARY KEY, registered_by INTEGER NOT NULL, registered_at TEXT NOT NULL, expires_at TEXT)",
            "CREATE TABLE IF NOT EXISTS licenses (license_key TEXT PRIMARY KEY, duration_days INTEGER NOT NULL, is_used INTEGER DEFAULT 0, used_by_guild INTEGER, used_at TEXT)",
            "CREATE TABLE IF NOT EXISTS guild_settings (guild_id INTEGER PRIMARY KEY, receipt_channel_id INTEGER, welcome_channel_id INTEGER, log_channel_id INTEGER, verify_role_id INTEGER, ticket_category_id INTEGER, ticket_role_id INTEGER, ticket_message TEXT)",
            "CREATE TABLE IF NOT EXISTS bot_admins (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, added_by INTEGER NOT NULL, added_at TEXT NOT NULL, PRIMARY KEY (guild_id, user_id))",
            "CREATE TABLE IF NOT EXISTS server_admins (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, added_by INTEGER NOT NULL, added_at TEXT NOT NULL, PRIMARY KEY (guild_id, user_id))",
            "CREATE TABLE IF NOT EXISTS bot_sellers (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, added_by INTEGER NOT NULL, added_at TEXT NOT NULL, PRIMARY KEY (guild_id, user_id))",
            "CREATE TABLE IF NOT EXISTS ticket_logs (channel_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL, owner_id INTEGER NOT NULL, opened_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS user_join_counts (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, join_count INTEGER DEFAULT 0, PRIMARY KEY (guild_id, user_id))",
            "CREATE TABLE IF NOT EXISTS verify_codes (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, code TEXT NOT NULL, PRIMARY KEY (guild_id, user_id))",
            "CREATE TABLE IF NOT EXISTS server_backups (backup_key TEXT PRIMARY KEY, guild_id INTEGER NOT NULL, backup_data TEXT NOT NULL, created_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS withdraw_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, amount INTEGER NOT NULL, status TEXT DEFAULT '대기중', created_at TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS suggestions (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL)"
        ]
        with DB.get_connection() as conn:
            for q in queries:
                conn.execute(q)
            # 기존 DB 호환을 위한 컬럼 추가 안전 장치
            try:
                conn.execute("ALTER TABLE guild_settings ADD COLUMN ticket_message TEXT")
            except sqlite3.OperationalError:
                pass
            conn.commit()

# ==============================================================================
# 3. 유틸리티 및 권한 검사 함수
# ==============================================================================
def get_user_points(guild_id: int, user_id: int) -> int:
    row = DB.fetchone("SELECT points FROM user_points WHERE guild_id = ? AND user_id = ?", guild_id, user_id)
    return row["points"] if row else 0

def is_guild_registered(guild_id: int) -> bool:
    row = DB.fetchone("SELECT expires_at FROM registered_guilds WHERE guild_id = ?", guild_id)
    if not row: return False
    if not row["expires_at"]: return True
    exp_dt = datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
    return datetime.now(KST) < exp_dt

def is_bot_admin(user: discord.Member, guild_id: int) -> bool:
    if getattr(user.guild_permissions, 'administrator', False) or any(r.name == ADMIN_ROLE_NAME for r in getattr(user, 'roles', [])):
        return True
    return bool(DB.fetchone("SELECT 1 FROM bot_admins WHERE guild_id = ? AND user_id = ?", guild_id, user.id))

def is_server_admin(user: discord.Member, guild_id: int) -> bool:
    if is_bot_admin(user, guild_id): return True
    return bool(DB.fetchone("SELECT 1 FROM server_admins WHERE guild_id = ? AND user_id = ?", guild_id, user.id))

def is_seller(user: discord.Member, guild_id: int) -> bool:
    if is_server_admin(user, guild_id): return True
    return bool(DB.fetchone("SELECT 1 FROM bot_sellers WHERE guild_id = ? AND user_id = ?", guild_id, user.id))

async def send_purchase_receipt(guild: discord.Guild, buyer: discord.abc.User, item_name: str, qty: int, price: int):
    row = DB.fetchone("SELECT receipt_channel_id FROM guild_settings WHERE guild_id = ?", guild.id)
    embed = discord.Embed(title="🧾 구매 영수증", color=discord.Color.green(), timestamp=datetime.now(KST))
    embed.set_author(name=str(buyer), icon_url=buyer.display_avatar.url)
    embed.add_field(name="구매자", value=buyer.mention, inline=True)
    embed.add_field(name="상품명", value=item_name, inline=True)
    embed.add_field(name="수량", value=f"{qty}개", inline=True)
    embed.add_field(name="총 결제 금액", value=f"**{fmt_won(price)}**", inline=True)

    channel = guild.get_channel(row["receipt_channel_id"]) if row and row["receipt_channel_id"] else None
    if channel:
        try:
            await channel.send(embed=embed)
            return "channel"
        except: pass
    try:
        embed.description = f"**{guild.name}** 서버에서의 구매 영수증입니다."
        await buyer.send(embed=embed)
        return "dm"
    except:
        return "failed"

def admin_only():
    async def predicate(interaction: discord.Interaction):
        if not is_guild_registered(interaction.guild_id):
            await interaction.response.send_message("⚠️ 라이센스 만료 또는 승인되지 않은 서버입니다.", ephemeral=True)
            return False
        if not is_server_admin(interaction.user, interaction.guild_id):
            await interaction.response.send_message("❌ 서버 관리자만 사용할 수 있습니다.", ephemeral=True)
            return False
        return True
    return app_commands.check(predicate)

def seller_only():
    async def predicate(interaction: discord.Interaction):
        if not is_guild_registered(interaction.guild_id):
            await interaction.response.send_message("⚠️ 라이센스 만료 또는 승인되지 않은 서버입니다.", ephemeral=True)
            return False
        if not is_seller(interaction.user, interaction.guild_id):
            await interaction.response.send_message("❌ 관리자 또는 판매자만 사용할 수 있습니다.", ephemeral=True)
            return False
        return True
    return app_commands.check(predicate)

# ==============================================================================
# 4. UI 컴포넌트 (Views & Modals)
# ==============================================================================
class VerifyModal(discord.ui.Modal, title="서버 회원 인증"):
    def __init__(self, target_code: str):
        super().__init__()
        self.code_input = discord.ui.TextInput(
            label="인증 번호 입력", placeholder=f"[{target_code}] 입력", min_length=4, max_length=4, required=True
        )
        self.add_item(self.code_input)

    async def on_submit(self, interaction: discord.Interaction):
        entered = self.code_input.value.strip()
        row = DB.fetchone("SELECT code FROM verify_codes WHERE guild_id = ? AND user_id = ?", interaction.guild.id, interaction.user.id)
        if not row or entered != row["code"]:
            return await interaction.response.send_message("❌ 인증 번호가 일치하지 않습니다.", ephemeral=True)

        set_row = DB.fetchone("SELECT verify_role_id FROM guild_settings WHERE guild_id = ?", interaction.guild.id)
        role = interaction.guild.get_role(set_row["verify_role_id"]) if set_row and set_row["verify_role_id"] else None

        if role:
            try:
                await interaction.user.add_roles(role)
                DB.execute("DELETE FROM verify_codes WHERE guild_id = ? AND user_id = ?", interaction.guild.id, interaction.user.id)
                await interaction.response.send_message(f"✅ 인증 완료! `{role.name}` 역할이 지급되었습니다.", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("⚠️ 봇의 권한/역할 순위가 낮아 지급할 수 없습니다.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ 서버에 인증 역할이 설정되지 않았습니다.", ephemeral=True)

class VerifyView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="인증하기 🔓", style=discord.ButtonStyle.green, custom_id="verify_button")
    async def verify_btn(self, interaction: discord.Interaction, btn: discord.ui.Button):
        code = "".join(random.choices(string.digits, k=4))
        DB.execute("INSERT INTO verify_codes (guild_id, user_id, code) VALUES (?, ?, ?) ON CONFLICT DO UPDATE SET code = ?", interaction.guild.id, interaction.user.id, code, code)
        await interaction.response.send_modal(VerifyModal(target_code=code))

class MainVendingView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="🛒 상품 구매", style=discord.ButtonStyle.blurple, custom_id="vending_buy")
    async def buy_btn(self, interaction: discord.Interaction, btn: discord.ui.Button):
        categories = DB.fetchall("SELECT DISTINCT category FROM prices WHERE guild_id = ?", interaction.guild_id)
        if not categories: return await interaction.response.send_message("❌ 등록된 카테고리가 없습니다.", ephemeral=True)

        view = discord.ui.View(timeout=180)
        select = discord.ui.Select(placeholder="카테고리를 선택하세요")
        for cat in categories: select.add_option(label=cat["category"], value=cat["category"])

        async def select_callback(inter: discord.Interaction):
            items = DB.fetchall("SELECT item, price, stock FROM prices WHERE guild_id = ? AND category = ?", inter.guild_id, select.values[0])
            if not items: return await inter.response.send_message("❌ 상품이 없습니다.", ephemeral=True)

            item_view = discord.ui.View(timeout=180)
            item_select = discord.ui.Select(placeholder="구매할 상품 선택")
            for it in items:
                stk = f"재고: {it['stock']}개" if it['stock'] != -1 else "무제한"
                item_select.add_option(label=it["item"], description=f"{fmt_won(it['price'])} | {stk}", value=it["item"])

            async def item_callback(i: discord.Interaction):
                item_name = item_select.values[0]
                with DB.get_connection() as conn:
                    it_info = conn.execute("SELECT price, stock FROM prices WHERE guild_id=? AND item=?", (i.guild_id, item_name)).fetchone()
                    if not it_info: return await i.response.send_message("❌ 상품을 찾을 수 없습니다.", ephemeral=True)
                    if it_info["stock"] != -1 and it_info["stock"] <= 0: return await i.response.send_message("❌ 품절된 상품입니다.", ephemeral=True)

                    user_pts = get_user_points(i.guild_id, i.user.id)
                    if user_pts < it_info["price"]: return await i.response.send_message("❌ 포인트가 부족합니다.", ephemeral=True)

                    if it_info["stock"] != -1:
                        cur = conn.execute("UPDATE prices SET stock=stock-1 WHERE guild_id=? AND item=? AND stock>0", (i.guild_id, item_name))
                        if cur.rowcount == 0: return await i.response.send_message("❌ 방금 품절되었습니다.", ephemeral=True)

                    conn.execute("UPDATE user_points SET points=points-? WHERE guild_id=? AND user_id=?", (it_info["price"], i.guild_id, i.user.id))
                    conn.execute("INSERT INTO transactions (guild_id, buyer_id, buyer_name, item, quantity, unit_price, total_price, memo, created_at, recorded_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                 (i.guild_id, i.user.id, i.user.display_name, item_name, 1, it_info["price"], it_info["price"], "자판기 구매", now_kst_str(), "System"))
                    conn.commit()

                res = await send_purchase_receipt(i.guild, i.user, item_name, 1, it_info["price"])
                msg = "✅ 구매 완료! " + ("지정 채널에 영수증 발급됨." if res=="channel" else "개인 DM으로 영수증 발송됨." if res=="dm" else "DM 전송 실패 (구매내역 확인요망).")
                await i.response.send_message(msg, ephemeral=True)

            item_select.callback = item_callback
            item_view.add_item(item_select)
            await inter.response.send_message("📂 상품을 선택하세요.", view=item_view, ephemeral=True)

        select.callback = select_callback
        view.add_item(select)
        await interaction.response.send_message("🛒 카테고리를 선택하세요.", view=view, ephemeral=True)

    @discord.ui.button(label="📋 상품 목록", style=discord.ButtonStyle.gray, custom_id="vending_products")
    async def list_btn(self, interaction: discord.Interaction, btn: discord.ui.Button):
        items = DB.fetchall("SELECT item, category, price, stock FROM prices WHERE guild_id = ?", interaction.guild_id)
        if not items: return await interaction.response.send_message("❌ 등록된 상품이 없습니다.", ephemeral=True)
        embed = discord.Embed(title="🛍️ 서버 상품 목록", color=discord.Color.blue())
        for it in items:
            stk = f"{it['stock']}개" if it['stock'] != -1 else "무제한"
            embed.add_field(name=f"[{it['category']}] {it['item']}", value=f"가격: **{fmt_won(it['price'])}** | 재고: {stk}", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="💰 포인트 충전 문의", style=discord.ButtonStyle.green, custom_id="vending_charge")
    async def charge_btn(self, interaction: discord.Interaction, btn: discord.ui.Button):
        await interaction.response.send_message("💬 포인트 충전은 서버 관리자에게 문의해주세요!", ephemeral=True)

# ------------------------------------------------------------------------------
# 4.1. 고급 티켓 시스템 UI (Views)
# ------------------------------------------------------------------------------
class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 티켓 닫기", style=discord.ButtonStyle.danger, custom_id="ticket_close_btn")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🔒 티켓을 종료합니다. 3초 후 채널이 삭제됩니다...", ephemeral=True)
        DB.execute("DELETE FROM ticket_logs WHERE channel_id = ?", interaction.channel.id)
        await asyncio.sleep(3)
        try:
            await interaction.channel.delete(reason=f"티켓 종료 (실행자: {interaction.user})")
        except:
            pass

class TicketSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(
        placeholder="🎫 문의 유형을 선택해주세요",
        custom_id="ticket_select_dropdown",
        options=[
            discord.SelectOption(label="상품 구매 및 충전 문의", description="포인트 충전 및 상품 구매 관련 문의입니다.", emoji="💳", value="purchase"),
            discord.SelectOption(label="일반 및 서버 문의", description="서버 이용에 대한 일반적인 질문입니다.", emoji="❓", value="general"),
            discord.SelectOption(label="기타 및 신고 문의", description="기타 건의사항이나 신고 내용입니다.", emoji="🚨", value="report")
        ]
    )
    async def ticket_select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        guild = interaction.guild
        user = interaction.user
        val = select.values[0]

        existing = DB.fetchone("SELECT channel_id FROM ticket_logs WHERE guild_id = ? AND owner_id = ?", guild.id, user.id)
        if existing:
            ch = guild.get_channel(existing["channel_id"])
            if ch:
                return await interaction.response.send_message(f"❌ 이미 열려있는 티켓 채널이 있습니다: {ch.mention}", ephemeral=True)
            else:
                DB.execute("DELETE FROM ticket_logs WHERE channel_id = ?", existing["channel_id"])

        settings = DB.fetchone("SELECT ticket_category_id, ticket_role_id FROM guild_settings WHERE guild_id = ?", guild.id)
        category = guild.get_category(settings["ticket_category_id"]) if settings and settings["ticket_category_id"] else None
        staff_role = guild.get_role(settings["ticket_role_id"]) if settings and settings["ticket_role_id"] else None

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)
        }
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        type_names = {"purchase": "상품구매-충전", "general": "일반문의", "report": "신고-기타"}
        channel_name = f"ticket-{type_names.get(val, '문의')}-{user.name}"

        try:
            ticket_channel = await guild.create_text_channel(name=channel_name, category=category, overwrites=overwrites)
        except Exception as e:
            return await interaction.response.send_message(f"❌ 티켓 채널 생성 실패: {e}", ephemeral=True)

        DB.execute("INSERT INTO ticket_logs (channel_id, guild_id, owner_id, opened_at) VALUES (?, ?, ?, ?)", ticket_channel.id, guild.id, user.id, now_kst_str())

        embed = discord.Embed(
            title=f"🎫 {user.display_name} 님의 문의 티켓",
            description=f"문의해주셔서 감사합니다! 담당자가 확인 후 답변해 드릴 예정입니다.\n\n"
                        f"• **문의자**: {user.mention}\n"
                        f"• **문의 분류**: {select.values[0]}\n\n"
                        f"하단의 **[티켓 닫기]** 버튼을 누르면 상담이 종료됩니다.",
            color=discord.Color.blue(),
            timestamp=datetime.now(KST)
        )
        ping_content = f"{user.mention}"
        if staff_role:
            ping_content += f" {staff_role.mention}"

        await ticket_channel.send(content=ping_content, embed=embed, view=TicketControlView())
        await interaction.response.send_message(f"✅ 티켓 채널이 생성되었습니다: {ticket_channel.mention}", ephemeral=True)

class LogAdminActionView(discord.ui.View):
    def __init__(self, target_user_id: int):
        super().__init__(timeout=None)
        self.target_id = target_user_id

    @discord.ui.button(label="추방(Kick)", style=discord.ButtonStyle.danger, custom_id="mod_kick")
    async def kick_btn(self, interaction: discord.Interaction, btn: discord.ui.Button):
        if not is_server_admin(interaction.user, interaction.guild_id):
            return await interaction.response.send_message("❌ 서버 관리자만 사용할 수 있습니다.", ephemeral=True)
        try:
            await interaction.guild.kick(discord.Object(id=self.target_id), reason=f"관리 패널 추방 (실행자: {interaction.user})")
            await interaction.response.send_message("✅ 추방 완료", ephemeral=True)
        except Exception as e: 
            await interaction.response.send_message(f"❌ 추방 실패: {e}", ephemeral=True)

    @discord.ui.button(label="차단(Ban)", style=discord.ButtonStyle.secondary, custom_id="mod_ban")
    async def ban_btn(self, interaction: discord.Interaction, btn: discord.ui.Button):
        if not is_server_admin(interaction.user, interaction.guild_id):
            return await interaction.response.send_message("❌ 서버 관리자만 사용할 수 있습니다.", ephemeral=True)
        try:
            await interaction.guild.ban(discord.Object(id=self.target_id), reason=f"관리 패널 차단 (실행자: {interaction.user})")
            await interaction.response.send_message("✅ 차단 완료", ephemeral=True)
        except Exception as e: 
            await interaction.response.send_message(f"❌ 차단 실패: {e}", ephemeral=True)

# ==============================================================================
# 5. Cogs (명령어 모듈화)
# ==============================================================================
class SystemCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="라이센스생성", description="새로운 서버 라이센스 키를 생성합니다.")
    @admin_only()
    async def create_license(self, interaction: discord.Interaction, 일수: int):
        if 일수 <= 0:
            return await interaction.response.send_message("❌ 라이센스 기간은 1일 이상이어야 합니다.", ephemeral=True)
        
        chars = string.ascii_uppercase + string.digits
        key_part = lambda: "".join(random.choices(chars, k=4))
        license_key = f"LIC-{key_part()}-{key_part()}-{key_part()}"
        
        DB.execute("INSERT INTO licenses (license_key, duration_days, is_used) VALUES (?, ?, 0)", license_key, 일수)
        
        embed = discord.Embed(title="🔑 라이센스 키 생성 완료", color=discord.Color.green())
        embed.add_field(name="라이센스 키", value=f"`{license_key}`", inline=False)
        embed.add_field(name="사용 기간", value=f"{일수}일", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="라이센스등록", description="서버 라이센스를 등록합니다.")
    async def register_license(self, interaction: discord.Interaction, 라이센스키: str):
        lic = DB.fetchone("SELECT * FROM licenses WHERE license_key = ? AND is_used = 0", 라이센스키)
        if not lic: return await interaction.response.send_message("❌ 유효하지 않거나 이미 사용된 키입니다.", ephemeral=True)
        cur_exp = DB.fetchone("SELECT expires_at FROM registered_guilds WHERE guild_id = ?", interaction.guild_id)
        
        start_dt = datetime.now(KST)
        if cur_exp and cur_exp["expires_at"]:
            dt = datetime.strptime(cur_exp["expires_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
            start_dt = max(start_dt, dt)
        
        exp_str = (start_dt + timedelta(days=lic["duration_days"])).strftime("%Y-%m-%d %H:%M:%S")
        DB.execute("UPDATE licenses SET is_used=1, used_by_guild=?, used_at=? WHERE license_key=?", interaction.guild_id, now_kst_str(), 라이센스키)
        DB.execute("INSERT INTO registered_guilds (guild_id, registered_by, registered_at, expires_at) VALUES (?,?,?,?) ON CONFLICT DO UPDATE SET expires_at=?", interaction.guild_id, interaction.user.id, now_kst_str(), exp_str, exp_str)
        await interaction.response.send_message(f"🎉 라이센스 연장 완료! (만료일: {exp_str})", ephemeral=True)

    @app_commands.command(name="서버정보", description="서버 상태와 라이센스를 확인합니다.")
    async def server_info(self, interaction: discord.Interaction):
        is_reg = is_guild_registered(interaction.guild_id)
        exp = DB.fetchone("SELECT expires_at FROM registered_guilds WHERE guild_id = ?", interaction.guild_id)
        embed = discord.Embed(title=f"📊 {interaction.guild.name} 서버 정보", color=discord.Color.blue())
        embed.add_field(name="라이센스 승인", value="✅ 승인됨" if is_reg else "❌ 미승인", inline=True)
        if exp and exp["expires_at"]: embed.add_field(name="만료일", value=exp["expires_at"], inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="서버백업", description="상점 및 서버의 역할, 카테고리, 채널 구조를 백업합니다.")
    @admin_only()
    async def backup_server(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        g = guild.id

        roles_data = []
        for role in sorted(guild.roles, key=lambda r: r.position, reverse=True):
            if role.is_default() or role.managed: continue
            roles_data.append({
                "name": role.name, "color": role.color.value, "hoist": role.hoist,
                "mentionable": role.mentionable, "permissions": role.permissions.value
            })

        categories_data = []
        no_cat_channels = []
        for ch in guild.channels:
            if isinstance(ch, discord.CategoryChannel):
                cat_channels = []
                for text_or_voice in ch.channels:
                    cat_channels.append({"name": text_or_voice.name, "type": "voice" if isinstance(text_or_voice, discord.VoiceChannel) else "text", "topic": getattr(text_or_voice, "topic", None)})
                categories_data.append({"name": ch.name, "channels": cat_channels})
            elif ch.category is None and not isinstance(ch, discord.CategoryChannel):
                no_cat_channels.append({"name": ch.name, "type": "voice" if isinstance(ch, discord.VoiceChannel) else "text", "topic": getattr(ch, "topic", None)})

        data = {
            "prices": [dict(r) for r in DB.fetchall("SELECT * FROM prices WHERE guild_id=?", g)],
            "settings": dict(DB.fetchone("SELECT * FROM guild_settings WHERE guild_id=?", g) or {}),
            "roles": roles_data, "categories": categories_data, "no_category_channels": no_cat_channels
        }

        bkey = f"BK-{''.join(random.choices(string.ascii_uppercase+string.digits, k=10))}"
        DB.execute("INSERT INTO server_backups (backup_key, guild_id, backup_data, created_at) VALUES (?,?,?,?)", bkey, g, json.dumps(data), now_kst_str())
        await interaction.followup.send(f"✅ 백업 완료!\n복구 키: `{bkey}`", ephemeral=True)

    @app_commands.command(name="서버복구", description="백업 키로 역할, 카테고리, 채널, 상점 데이터를 모두 복구합니다.")
    @admin_only()
    async def restore_server(self, interaction: discord.Interaction, 백업키: str):
        await interaction.response.defer(ephemeral=True)
        row = DB.fetchone("SELECT backup_data FROM server_backups WHERE backup_key=?", 백업키)
        if not row: return await interaction.followup.send("❌ 유효하지 않은 백업 키입니다.", ephemeral=True)
        
        data = json.loads(row["backup_data"])
        guild = interaction.guild

        DB.execute("DELETE FROM prices WHERE guild_id=?", guild.id)
        for p in data.get("prices", []):
            DB.execute("INSERT INTO prices (guild_id, item, category, price, stock) VALUES (?,?,?,?,?)", guild.id, p["item"], p.get("category","기타"), p["price"], p["stock"])

        for r_info in data.get("roles", []):
            try:
                await guild.create_role(name=r_info["name"], color=discord.Color(r_info["color"]), hoist=r_info["hoist"], mentionable=r_info["mentionable"], permissions=discord.Permissions(r_info["permissions"]))
            except: pass

        for cat_info in data.get("categories", []):
            try:
                new_cat = await guild.create_category(cat_info["name"])
                for ch_info in cat_info.get("channels", []):
                    if ch_info["type"] == "voice": await guild.create_voice_channel(ch_info["name"], category=new_cat)
                    else: await guild.create_text_channel(ch_info["name"], category=new_cat, topic=ch_info.get("topic"))
            except: pass

        for ch_info in data.get("no_category_channels", []):
            try:
                if ch_info["type"] == "voice": await guild.create_voice_channel(ch_info["name"])
                else: await guild.create_text_channel(ch_info["name"], topic=ch_info.get("topic"))
            except: pass

        await interaction.followup.send("✅ 서버 구조 및 데이터 복구 완료!", ephemeral=True)

    @app_commands.command(name="공지발송", description="임베드로 공지를 발송합니다.")
    @admin_only()
    async def send_notice(self, interaction: discord.Interaction, 내용: str):
        embed = discord.Embed(title="📢 공지사항", description=내용, color=discord.Color.red())
        await interaction.channel.send(embed=embed)
        await interaction.response.send_message("✅ 전송 완료", ephemeral=True)

    @app_commands.command(name="청소하기", description="채팅을 삭제합니다.")
    @admin_only()
    async def clear_msg(self, interaction: discord.Interaction, 개수: int):
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=min(개수, 100))
        await interaction.followup.send(f"✅ {len(deleted)}개 삭제 완료.", ephemeral=True)

class EconomyCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="포인트조회", description="내 포인트를 확인합니다.")
    async def check_pts(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"💰 내 포인트: **{fmt_won(get_user_points(interaction.guild_id, interaction.user.id))}**", ephemeral=True)

    @app_commands.command(name="내정보", description="내 활동 프로필을 확인합니다.")
    async def my_info(self, interaction: discord.Interaction):
        pts = get_user_points(interaction.guild_id, interaction.user.id)
        tx = DB.fetchone("SELECT COUNT(*) as c FROM transactions WHERE guild_id=? AND buyer_id=?", interaction.guild_id, interaction.user.id)["c"]
        embed = discord.Embed(title=f"👤 {interaction.user.display_name} 님 정보", color=discord.Color.blue())
        embed.add_field(name="💰 보유 포인트", value=fmt_won(pts), inline=True)
        embed.add_field(name="🛒 누적 구매", value=f"{tx}회", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="내구매내역", description="최근 구매 기록을 봅니다.")
    async def my_tx(self, interaction: discord.Interaction):
        rows = DB.fetchall("SELECT item, total_price, created_at FROM transactions WHERE guild_id=? AND buyer_id=? ORDER BY id DESC LIMIT 5", interaction.guild_id, interaction.user.id)
        if not rows: return await interaction.response.send_message("❌ 내역이 없습니다.", ephemeral=True)
        embed = discord.Embed(title="📦 구매 내역", color=discord.Color.blue())
        for r in rows: embed.add_field(name=r["item"], value=f"{fmt_won(r['total_price'])} | {r['created_at']}", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="출금신청", description="포인트를 현금 환전/출금합니다.")
    async def withdraw_pts(self, interaction: discord.Interaction, 금액: int):
        if 금액 <= 0 or get_user_points(interaction.guild_id, interaction.user.id) < 금액:
            return await interaction.response.send_message("❌ 올바른 금액 또는 잔액 부족.", ephemeral=True)
        DB.execute("INSERT INTO withdraw_requests (guild_id, user_id, amount, created_at) VALUES (?,?,?,?)", interaction.guild_id, interaction.user.id, 금액, now_kst_str())
        await interaction.response.send_message(f"✅ {fmt_won(금액)} 출금 신청 완료.", ephemeral=True)

    @app_commands.command(name="송금하기", description="포인트를 선물합니다.")
    async def send_pts(self, interaction: discord.Interaction, 유저: discord.Member, 금액: int):
        if 금액 <= 0 or get_user_points(interaction.guild_id, interaction.user.id) < 금액 or 유저.bot or 유저 == interaction.user:
            return await interaction.response.send_message("❌ 송금 불가 조건입니다.", ephemeral=True)
        DB.execute("UPDATE user_points SET points=points-? WHERE guild_id=? AND user_id=?", 금액, interaction.guild_id, interaction.user.id)
        DB.execute("INSERT INTO user_points (guild_id, user_id, points) VALUES (?,?,?) ON CONFLICT DO UPDATE SET points=points+?", interaction.guild_id, 유저.id, 금액, 금액)
        await interaction.response.send_message(f"✅ {유저.mention}님께 {fmt_won(금액)} 송금 완료.", ephemeral=True)

    @app_commands.command(name="포인트지급", description="관리자가 포인트를 지급합니다.")
    @seller_only()
    async def admin_give_pts(self, interaction: discord.Interaction, 유저: discord.Member, 금액: int):
        DB.execute("INSERT INTO user_points (guild_id, user_id, points) VALUES (?,?,?) ON CONFLICT DO UPDATE SET points=points+?", interaction.guild_id, 유저.id, 금액, 금액)
        await interaction.response.send_message(f"✅ {유저.mention}님께 {fmt_won(금액)} 지급 완료.", ephemeral=True)

    @app_commands.command(name="포인트차감", description="관리자가 포인트를 차감합니다.")
    @seller_only()
    async def admin_sub_pts(self, interaction: discord.Interaction, 유저: discord.Member, 금액: int):
        DB.execute("UPDATE user_points SET points=MAX(0, points-?) WHERE guild_id=? AND user_id=?", 금액, interaction.guild_id, 유저.id)
        await interaction.response.send_message(f"✅ {유저.mention}님 포인트 {fmt_won(금액)} 차감 완료.", ephemeral=True)

class ShopCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="상점목록", description="판매 상품을 조회합니다.")
    async def shop_list(self, interaction: discord.Interaction):
        items = DB.fetchall("SELECT item, category, price, stock FROM prices WHERE guild_id=?", interaction.guild_id)
        if not items: return await interaction.response.send_message("❌ 상품이 없습니다.", ephemeral=True)
        embed = discord.Embed(title="🛍️ 상품 목록", color=discord.Color.green())
        for it in items: embed.add_field(name=f"[{it['category']}] {it['item']}", value=f"{fmt_won(it['price'])} | 재고: {it['stock'] if it['stock']!=-1 else '무제한'}", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="상품검색", description="상품을 검색합니다.")
    async def search_item(self, interaction: discord.Interaction, 검색어: str):
        items = DB.fetchall("SELECT item, category, price FROM prices WHERE guild_id=? AND item LIKE ?", interaction.guild_id, f"%{검색어}%")
        if not items: return await interaction.response.send_message("❌ 결과 없음.", ephemeral=True)
        embed = discord.Embed(title=f"🔍 '{검색어}' 검색 결과", color=discord.Color.blue())
        for it in items: embed.add_field(name=f"[{it['category']}] {it['item']}", value=fmt_won(it['price']), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="상품등록", description="새 상품을 상점에 올립니다.")
    @seller_only()
    async def add_item(self, interaction: discord.Interaction, 카테고리: str, 상품명: str, 가격: int, 재고: int = -1):
        DB.execute("INSERT INTO prices (guild_id, item, category, price, stock) VALUES (?,?,?,?,?) ON CONFLICT DO UPDATE SET category=?, price=?, stock=?", interaction.guild_id, 상품명, 카테고리, 가격, 재고, 카테고리, 가격, 재고)
        await interaction.response.send_message(f"✅ [{카테고리}] {상품명} ({fmt_won(가격)}) 등록 완료.", ephemeral=True)

    @app_commands.command(name="재고수정", description="상품 재고를 특정 수량으로 덮어씁니다.")
    @seller_only()
    async def set_stock(self, interaction: discord.Interaction, 상품명: str, 재고: int):
        res = DB.execute("UPDATE prices SET stock=? WHERE guild_id=? AND item=?", 재고, interaction.guild_id, 상품명)
        if res == 0: return await interaction.response.send_message("❌ 상품을 찾을 수 없습니다.", ephemeral=True)
        await interaction.response.send_message(f"✅ {상품명} 재고가 {재고}개로 변경됨.", ephemeral=True)

    @app_commands.command(name="재고추가", description="상품 재고를 늘립니다.")
    @seller_only()
    async def add_stock(self, interaction: discord.Interaction, 상품명: str, 수량: int):
        res = DB.execute("UPDATE prices SET stock=stock+? WHERE guild_id=? AND item=? AND stock != -1", 수량, interaction.guild_id, 상품명)
        if res == 0: return await interaction.response.send_message("❌ 무제한 상품이거나 상품이 없습니다.", ephemeral=True)
        await interaction.response.send_message(f"✅ {상품명} 재고 {수량}개 추가됨.", ephemeral=True)

    @app_commands.command(name="재고차감", description="상품 재고를 줄립니다.")
    @seller_only()
    async def sub_stock(self, interaction: discord.Interaction, 상품명: str, 수량: int):
        res = DB.execute("UPDATE prices SET stock=MAX(0, stock-?) WHERE guild_id=? AND item=? AND stock != -1", 수량, interaction.guild_id, 상품명)
        if res == 0: return await interaction.response.send_message("❌ 무제한 상품이거나 상품이 없습니다.", ephemeral=True)
        await interaction.response.send_message(f"✅ {상품명} 재고 {수량}개 차감됨.", ephemeral=True)

    @app_commands.command(name="자판기패널전송", description="자판기 UI 패널을 설치합니다.")
    @admin_only()
    async def send_vending(self, interaction: discord.Interaction):
        embed = discord.Embed(title="🛒 자판기", description="버튼을 눌러 상품을 구매하세요.", color=discord.Color.from_rgb(52, 152, 219))
        await interaction.channel.send(embed=embed, view=MainVendingView())
        await interaction.response.send_message("✅ 자판기 전송 완료", ephemeral=True)

class TicketCog(commands.Cog):
    """티켓 패널 및 설정 모듈"""
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="티켓패널", description="고급 티켓 생성 패널을 현재 채널에 전송합니다.")
    @admin_only()
    async def send_ticket_panel(self, interaction: discord.Interaction):
        # 저장된 커스텀 메시지가 있으면 불러오고, 없으면 기본 메시지 사용
        row = DB.fetchone("SELECT ticket_message FROM guild_settings WHERE guild_id = ?", interaction.guild_id)
        custom_desc = row["ticket_message"] if row and row["ticket_message"] else (
            "서버 이용 중 도움이 필요하시거나 상품 관련 문의가 있으신가요?\n"
            "아래 메뉴에서 **문의 유형**을 선택하시면 전용 1:1 상담 채널이 자동으로 생성됩니다!\n\n"
            "• **상품 구매 및 충전 문의** 💳\n"
            "• **일반 및 서버 문의** ❓\n"
            "• **기타 및 신고 문의** 🚨"
        )

        embed = discord.Embed(
            title="🎫 고객 지원 및 문의 센터",
            description=custom_desc,
            color=discord.Color.blurple(),
            timestamp=datetime.now(KST)
        )
        embed.set_footer(text="상담 채널은 당사자 외에 지정된 관리자만 볼 수 있습니다.")
        await interaction.channel.send(embed=embed, view=TicketSelectView())
        await interaction.response.send_message("✅ 티켓 패널이 성공적으로 전송되었습니다.", ephemeral=True)

    @app_commands.command(name="티켓패널설정", description="티켓 패널의 관리 역할, 안내 메시지, 생성 카테고리를 설정합니다.")
    @admin_only()
    async def set_ticket_config(
        self, 
        interaction: discord.Interaction, 
        카테고리: Optional[discord.CategoryChannel] = None, 
        역할: Optional[discord.Role] = None,
        메시지: Optional[str] = None
    ):
        cat_id = 카테고리.id if 카테고리 else None
        role_id = 역할.id if 역할 else None

        DB.execute(
            "INSERT INTO guild_settings (guild_id, ticket_category_id, ticket_role_id, ticket_message) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET "
            "ticket_category_id = COALESCE(?, ticket_category_id), "
            "ticket_role_id = COALESCE(?, ticket_role_id), "
            "ticket_message = COALESCE(?, ticket_message)",
            interaction.guild_id, cat_id, role_id, 메시지, cat_id, role_id, 메시지
        )

        msg = "⚙️ **티켓 설정이 성공적으로 업데이트되었습니다!**\n"
        if 카테고리: msg += f"• 생성 카테고리: `{카테고리.name}`\n"
        if 역할: msg += f"• 관리하는 스태프 역할: `{역할.name}`\n"
        if 메시지: msg += f"• 패널 안내 메시지: `{메시지}`\n"
        await interaction.response.send_message(msg, ephemeral=True)

class AdminSetupCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="봇관리자등록", description="봇 권한 관리자를 지정합니다.")
    @admin_only()
    async def add_bot_admin(self, interaction: discord.Interaction, 유저: discord.Member):
        DB.execute("INSERT INTO bot_admins (guild_id, user_id, added_by, added_at) VALUES (?,?,?,?) ON CONFLICT DO NOTHING", interaction.guild_id, 유저.id, interaction.user.id, now_kst_str())
        await interaction.response.send_message(f"✅ {유저.mention} 봇 관리자 등록 완료.", ephemeral=True)

    @app_commands.command(name="서버관리자등록", description="서버 관리자를 지정합니다.")
    @admin_only()
    async def add_srv_admin(self, interaction: discord.Interaction, 유저: discord.Member):
        DB.execute("INSERT INTO server_admins (guild_id, user_id, added_by, added_at) VALUES (?,?,?,?) ON CONFLICT DO NOTHING", interaction.guild_id, 유저.id, interaction.user.id, now_kst_str())
        await interaction.response.send_message(f"✅ {유저.mention} 서버 관리자 등록 완료.", ephemeral=True)

    @app_commands.command(name="판매자등록", description="상점 판매자를 등록합니다.")
    @admin_only()
    async def add_seller(self, interaction: discord.Interaction, 유저: discord.Member):
        DB.execute("INSERT INTO bot_sellers (guild_id, user_id, added_by, added_at) VALUES (?,?,?,?) ON CONFLICT DO NOTHING", interaction.guild_id, 유저.id, interaction.user.id, now_kst_str())
        await interaction.response.send_message(f"✅ {유저.mention} 판매자 등록 완료.", ephemeral=True)

    @app_commands.command(name="영수증채널설정", description="구매 영수증 로그 채널 지정")
    @admin_only()
    async def set_receipt(self, interaction: discord.Interaction, 채널: discord.TextChannel):
        DB.execute("INSERT INTO guild_settings (guild_id, receipt_channel_id) VALUES (?,?) ON CONFLICT DO UPDATE SET receipt_channel_id=?", interaction.guild_id, 채널.id, 채널.id)
        await interaction.response.send_message(f"✅ 영수증 채널 설정: {채널.mention}", ephemeral=True)

    @app_commands.command(name="입퇴장로그설정", description="유저 입퇴장/밴 알림 채널 지정")
    @admin_only()
    async def set_log(self, interaction: discord.Interaction, 채널: discord.TextChannel):
        DB.execute("INSERT INTO guild_settings (guild_id, log_channel_id) VALUES (?,?) ON CONFLICT DO UPDATE SET log_channel_id=?", interaction.guild_id, 채널.id, 채널.id)
        await interaction.response.send_message(f"✅ 로그 채널 설정: {채널.mention}", ephemeral=True)

    @app_commands.command(name="인증역할설정", description="인증 완료 시 줄 역할")
    @admin_only()
    async def set_vrole(self, interaction: discord.Interaction, 역할: discord.Role):
        DB.execute("INSERT INTO guild_settings (guild_id, verify_role_id) VALUES (?,?) ON CONFLICT DO UPDATE SET verify_role_id=?", interaction.guild_id, 역할.id, 역할.id)
        await interaction.response.send_message(f"✅ 인증 역할 설정: {역할.name}", ephemeral=True)

    @app_commands.command(name="인증패널전송", description="유저 인증 버튼 설치")
    @admin_only()
    async def send_vpanel(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        embed = discord.Embed(title="🔒 유저 인증", description="버튼을 눌러 인증하세요.", color=discord.Color.green())
        await interaction.channel.send(embed=embed, view=VerifyView())
        await interaction.followup.send("✅ 패널 전송 완료.", ephemeral=True)

class OwnerPrefixCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @commands.command(name="서버등록")
    async def reg_srv(self, ctx, gid: str = None, days: int = 30):
        if not (await self.bot.is_owner(ctx.author) or is_bot_admin(ctx.author, ctx.guild.id)): return
        tgt = int(gid) if gid else ctx.guild.id
        exp = (datetime.now(KST) + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        DB.execute("INSERT INTO registered_guilds (guild_id, registered_by, registered_at, expires_at) VALUES (?,?,?,?) ON CONFLICT DO UPDATE SET expires_at=?", tgt, ctx.author.id, now_kst_str(), exp, exp)
        await ctx.send(f"✅ 서버({tgt}) 등록됨. 만료: {exp}")

    @commands.command(name="강제동기화")
    async def force_sync(self, ctx):
        if not (await self.bot.is_owner(ctx.author) or is_bot_admin(ctx.author, ctx.guild.id)): return
        msg = await ctx.send("🔄 동기화 중...")
        await self.bot.tree.sync()
        await msg.edit(content="✅ 글로벌 재동기화 완료!")

# ==============================================================================
# 6. 메인 봇 클래스 및 이벤트
# ==============================================================================
class DinoBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        DB.init_db()
        await self.add_cog(SystemCog(self))
        await self.add_cog(EconomyCog(self))
        await self.add_cog(ShopCog(self))
        await self.add_cog(TicketCog(self))
        await self.add_cog(AdminSetupCog(self))
        await self.add_cog(OwnerPrefixCog(self))
        
        self.add_view(MainVendingView())
        self.add_view(VerifyView())
        self.add_view(TicketSelectView())
        self.add_view(TicketControlView())

bot = DinoBot()

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    await bot.process_commands(message)

@bot.event
async def on_member_join(member: discord.Member):
    DB.execute("INSERT INTO user_join_counts (guild_id, user_id, join_count) VALUES (?, ?, 1) ON CONFLICT DO UPDATE SET join_count = join_count + 1", member.guild.id, member.id)
    row_cnt = DB.fetchone("SELECT join_count FROM user_join_counts WHERE guild_id=? AND user_id=?", member.guild.id, member.id)
    join_count = row_cnt["join_count"] if row_cnt else 1

    row = DB.fetchone("SELECT log_channel_id FROM guild_settings WHERE guild_id=?", member.guild.id)
    if row and row["log_channel_id"]:
        ch = member.guild.get_channel(row["log_channel_id"])
        if ch:
            embed = discord.Embed(title="📥 멤버 가입", description=f"{member.mention} 님이 입장하셨습니다.", color=discord.Color.green(), timestamp=datetime.now(KST))
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.add_field(name="🔄 방문 횟수", value=f"총 {join_count}번째 입장", inline=False)
            await ch.send(embed=embed)

@bot.event
async def on_member_remove(member: discord.Member):
    row_cnt = DB.fetchone("SELECT join_count FROM user_join_counts WHERE guild_id=? AND user_id=?", member.guild.id, member.id)
    join_count = row_cnt["join_count"] if row_cnt else 1

    row = DB.fetchone("SELECT log_channel_id FROM guild_settings WHERE guild_id=?", member.guild.id)
    if not row or not row["log_channel_id"]: return
    ch = member.guild.get_channel(row["log_channel_id"])
    if not ch: return

    embed = discord.Embed(title="👏 멤버 퇴장 (관리 패널)", description=f"{member.name} 님이 떠나셨습니다.", color=discord.Color.red(), timestamp=datetime.now(KST))
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="🔄 총 방문 횟수", value=f"총 {join_count}회 드나듦", inline=False)
    await ch.send(embed=embed, view=LogAdminActionView(member.id))

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("❌ DISCORD_TOKEN 설정 필요.")
    bot.run(TOKEN)
