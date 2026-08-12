# -*- coding: utf-8 -*-
import os
import sqlite3
import asyncio
import secrets
import string
import random
from datetime import datetime, timezone, timedelta

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# 환경변수 및 기본 설정
# ---------------------------------------------------------------------------
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "YOUR_BOT_TOKEN_HERE") # 토큰을 직접 넣거나 .env 파일 활용
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

        cmd_name = interaction.data.get("name") if interaction.data else None
        if cmd_name == "라이센스등록":
            return True

        if not is_guild_registered(interaction.guild_id):
            await interaction.response.send_message(
                "⚠️ 이 서버는 사용 승인이 되지 않았거나 라이센스가 만료되었습니다.\n"
                "- 봇 개발자의 직인 승인(`!서버등록`) 또는 `/라이센스등록` 명령어를 이용해주세요.",
                ephemeral=True,
            )
            return False

        # 자판기, 티켓, 인증, 동적 알림 버튼 상호작용은 일반 유저 누구나 허용
        custom_id = interaction.data.get("custom_id") if interaction.data else ""
        if custom_id in [
            "btn_standard", "btn_custom", "btn_role", "vending_buy", "vending_products", 
            "vending_charge", "vending_info", "select_category", "select_buy_item", 
            "confirm_buy_item", "open_ticket", "close_ticket", "ticket_buy", 
            "select_ticket_item", "verify_button", "dyn_notif_clear_all"
        ] or custom_id.startswith("dyn_notif_"):
            return True

        # 슬래시 명령어 자체의 권한 체크
        if interaction.type == discord.InteractionType.application_command:
            # 일반 유저용 명령어 제외
            if cmd_name in ["포인트조회", "내구매내역", "라이센스등록"]:
                return True
                
            if not is_admin_or_seller(interaction):
                await interaction.response.send_message(
                    "❌ 이 기능은 관리자 또는 등록된 판매자만 사용할 수 있어요.",
                    ephemeral=True,
                )
                return False

        return True

bot = commands.Bot(command_prefix="!", intents=intents, tree_cls=GatedCommandTree)

# ---------------------------------------------------------------------------
# 데이터베이스 초기화 및 마이그레이션
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
            receipt_channel_id INTEGER
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

    try:
        cur.execute("ALTER TABLE prices ADD COLUMN role_id INTEGER DEFAULT NULL")
    except sqlite3.OperationalError:
        pass

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

def register_guild(guild_id: int, by_id: int, expires_at: str = None):
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO registered_guilds (guild_id, registered_by, registered_at, expires_at) 
        VALUES (?, ?, ?, ?) 
        ON CONFLICT(guild_id) DO UPDATE SET registered_by=?, registered_at=?, expires_at=?
        """,
        (guild_id, by_id, now_kst_str(), expires_at, by_id, now_kst_str(), expires_at),
    )
    conn.commit()
    conn.close()

def is_bot_admin(guild_id: int, user_id: int) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM bot_admins WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)).fetchone()
    conn.close()
    return row is not None

def is_bot_seller(guild_id: int, user_id: int) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM bot_sellers WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)).fetchone()
    conn.close()
    return row is not None

def add_bot_seller(guild_id: int, user_id: int, added_by: int):
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO bot_sellers (guild_id, user_id, added_by, added_at) VALUES (?, ?, ?, ?)",
        (guild_id, user_id, added_by, now_kst_str()),
    )
    conn.commit()
    conn.close()

def is_admin(ctx_or_interaction) -> bool:
    if isinstance(ctx_or_interaction, discord.Interaction):
        member = ctx_or_interaction.user
        guild_id = ctx_or_interaction.guild_id
    else:
        member = ctx_or_interaction.author
        guild_id = ctx_or_interaction.guild.id if ctx_or_interaction.guild else None

    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.administrator:
        return True
    if any(role.name == ADMIN_ROLE_NAME for role in member.roles):
        return True
    if guild_id and is_bot_admin(guild_id, member.id):
        return True
    return False

def is_admin_or_seller(ctx_or_interaction) -> bool:
    if is_admin(ctx_or_interaction):
        return True
    if isinstance(ctx_or_interaction, discord.Interaction):
        guild_id = ctx_or_interaction.guild_id
        user_id = ctx_or_interaction.user.id
    else:
        guild_id = ctx_or_interaction.guild.id if ctx_or_interaction.guild else None
        user_id = ctx_or_interaction.author.id
    
    if guild_id and is_bot_seller(guild_id, user_id):
        return True
    return False

def admin_or_seller_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if is_admin_or_seller(interaction):
            return True
        await interaction.response.send_message("❌ 이 명령어는 관리자 또는 등록된 판매자만 사용할 수 있어요.", ephemeral=True)
        return False
    return app_commands.check(predicate)

def fmt_won(n: int) -> str:
    return f"{n:,}원"

def now_kst_str() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

def get_user_points(guild_id: int, user_id: int) -> int:
    conn = get_conn()
    row = conn.execute("SELECT points FROM user_points WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)).fetchone()
    conn.close()
    return row["points"] if row else 0

def get_user_tier_info(guild_id: int, user_id: int) -> dict:
    conn = get_conn()
    row = conn.execute(
        """
        SELECT COUNT(*) as count, COALESCE(SUM(total_price), 0) as total_spent
        FROM transactions
        WHERE guild_id = ? AND buyer_id = ?
        """,
        (guild_id, user_id)
    ).fetchone()
    conn.close()

    count = row["count"] if row else 0
    total_spent = row["total_spent"] if row else 0

    tiers = [
        ("💎", "다이아몬드", 300000, 50, 0.15),
        ("👑", "플래티넘", 100000, 20, 0.10),
        ("🥇", "골드", 50000, 10, 0.08),
        ("🥈", "실버", 30000, 5, 0.05),
        ("🥉", "브론즈", 10000, 3, 0.02),
    ]

    for i, (icon, name, req_spent, req_count, rate) in enumerate(tiers):
        if total_spent >= req_spent or count >= req_count:
            if i > 0:
                next_t = tiers[i - 1]
                next_goal = f"다음 등급: {next_t[0]} **{next_t[1]}** (필요: {fmt_won(next_t[2])} 또는 {next_t[3]}회)"
            else:
                next_goal = "✨ **최고 등급(다이아몬드) 달성!**"

            return {
                "icon": icon,
                "name": name,
                "discount_rate": rate,
                "count": count,
                "total_spent": total_spent,
                "next_goal": next_goal
            }

    return {
        "icon": "🌱",
        "name": "일반 회원",
        "discount_rate": 0.0,
        "count": count,
        "total_spent": total_spent,
        "next_goal": "다음 등급: 🥉 **브론즈** (필요: 10,000원 또는 3회 구매)"
    }

@bot.event
async def on_ready():
    init_db()
    # 지속성 버튼 뷰 등록
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

# ---------------------------------------------------------------------------
# 보안 인증 UI
# ---------------------------------------------------------------------------
class VerifyModal(discord.ui.Modal):
    def __init__(self, target_number: int):
        super().__init__(title="🔒 서버 보안 회원 인증")
        self.target_number = target_number

        self.user_answer = discord.ui.TextInput(
            label=f"아래 인증 숫자를 입력해 주세요: [{target_number}]",
            placeholder=str(target_number),
            required=True,
            min_length=4,
            max_length=4
        )
        self.add_item(self.user_answer)

    async def on_submit(self, interaction: discord.Interaction):
        typed_val = self.user_answer.value.strip()

        if typed_val == str(self.target_number):
            role = discord.utils.get(interaction.guild.roles, name=VERIFY_ROLE_NAME)
            if role:
                try:
                    await interaction.user.add_roles(role)
                    await interaction.response.send_message(
                        f"✅ **인증 완료!** `{VERIFY_ROLE_NAME}` 역할을 받으셨습니다.", 
                        ephemeral=True
                    )
                except discord.Forbidden:
                    await interaction.response.send_message("⚠️ 봇의 권한이 부족하여 역할을 부여할 수 없습니다.", ephemeral=True)
            else:
                await interaction.response.send_message(f"⚠️ 서버에 `{VERIFY_ROLE_NAME}` 역할이 존재하지 않습니다.", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ **인증 실패!** 입력하신 숫자가 일치하지 않습니다.", ephemeral=True)

class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="인증하기 🔓", style=discord.ButtonStyle.green, custom_id="verify_button")
    async def verify_button_click(self, interaction: discord.Interaction, button: discord.ui.Button):
        random_code = random.randint(1000, 9999)
        await interaction.response.send_modal(VerifyModal(random_code))

# ---------------------------------------------------------------------------
# 동적 알림 역할 버튼 UI
# ---------------------------------------------------------------------------
class DynamicNotificationButton(discord.ui.Button):
    def __init__(self, label: str, role_id: int, style: discord.ButtonStyle = discord.ButtonStyle.primary):
        super().__init__(label=label, style=style, custom_id=f"dyn_notif_{role_id}")
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction):
        role = interaction.guild.get_role(self.role_id)
        if not role:
            await interaction.response.send_message("❌ 부여할 역할을 서버에서 찾을 수 없습니다.", ephemeral=True)
            return

        if role in interaction.user.roles:
            await interaction.user.remove_roles(role)
            await interaction.response.send_message(f"🔕 {role.mention} 역할이 **해제**되었습니다.", ephemeral=True)
        else:
            await interaction.user.add_roles(role)
            await interaction.response.send_message(f"🔔 {role.mention} 역할이 **부여**되었습니다!", ephemeral=True)

class ClearAllNotificationButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🧹 핑지우개", style=discord.ButtonStyle.secondary, custom_id="dyn_notif_clear_all")

    async def callback(self, interaction: discord.Interaction):
        removed_roles = []
        for child in self.view.children:
            if isinstance(child, DynamicNotificationButton):
                role = interaction.guild.get_role(child.role_id)
                if role and role in interaction.user.roles:
                    await interaction.user.remove_roles(role)
                    removed_roles.append(role.mention)

        if removed_roles:
            await interaction.response.send_message(f"🧹 알림 역할이 모두 제거되었습니다: {', '.join(removed_roles)}", ephemeral=True)
        else:
            await interaction.response.send_message("🧹 제거할 알림 역할이 없습니다.", ephemeral=True)

# ---------------------------------------------------------------------------
# 개발자 및 라이센스 명령어
# ---------------------------------------------------------------------------
@bot.command(name="서버등록")
async def register_server(ctx: commands.Context):
    if ctx.guild is None:
        return
    if not await bot.is_owner(ctx.author):
        await ctx.reply("❌ 이 명령어는 봇 개발자만 사용할 수 있습니다.")
        return

    register_guild(ctx.guild.id, ctx.author.id, expires_at=None)
    await ctx.reply(f"✅ **[{ctx.guild.name}]** 서버 등록이 완료되었습니다!")

@bot.tree.command(name="라이센스생성", description="[개발자] 신규 이용 라이센스 키를 발급합니다.")
@app_commands.describe(일수="유효 기간(일 단위)")
async def create_license(interaction: discord.Interaction, 일수: int):
    if not await bot.is_owner(interaction.user):
        await interaction.response.send_message("❌ 이 명령어는 봇 개발자만 사용할 수 있습니다.", ephemeral=True)
        return

    key = generate_license_key()
    conn = get_conn()
    conn.execute("INSERT INTO licenses (license_key, duration_days) VALUES (?, ?)", (key, 일수))
    conn.commit()
    conn.close()

    await interaction.response.send_message(f"🔑 **라이센스 키 발급 완료**\n- 키: `{key}`\n- 유효기간: **{일수}일**", ephemeral=True)

@bot.tree.command(name="라이센스등록", description="발급받은 라이센스 키를 통해 서버 사용 권한을 활성화합니다.")
@app_commands.describe(라이센스키="발급받은 라이센스 키")
async def redeem_license(interaction: discord.Interaction, 라이센스키: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 서버 관리자만 라이센스를 등록할 수 있습니다.", ephemeral=True)
        return

    key = 라이센스키.strip()
    conn = get_conn()
    lic = conn.execute("SELECT * FROM licenses WHERE license_key = ?", (key,)).fetchone()

    if not lic or lic["is_used"]:
        conn.close()
        await interaction.response.send_message("❌ 올바르지 않거나 이미 사용된 라이센스 키입니다.", ephemeral=True)
        return

    duration = lic["duration_days"]
    now_dt = datetime.now(KST)

    current_reg = conn.execute("SELECT expires_at FROM registered_guilds WHERE guild_id = ?", (interaction.guild_id,)).fetchone()
    if current_reg and current_reg["expires_at"]:
        cur_exp = datetime.strptime(current_reg["expires_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
        start_dt = max(now_dt, cur_exp)
    else:
        start_dt = now_dt

    exp_dt = start_dt + timedelta(days=duration)
    exp_str = exp_dt.strftime("%Y-%m-%d %H:%M:%S")

    conn.execute("UPDATE licenses SET is_used = 1, used_by_guild = ?, used_at = ? WHERE license_key = ?", (interaction.guild_id, now_kst_str(), key))
    conn.execute(
        "INSERT INTO registered_guilds (guild_id, registered_by, registered_at, expires_at) VALUES (?, ?, ?, ?) ON CONFLICT(guild_id) DO UPDATE SET expires_at = ?",
        (interaction.guild_id, interaction.user.id, now_kst_str(), exp_str, exp_str)
    )
    conn.commit()
    conn.close()

    await interaction.response.send_message(f"🎉 **라이센스 등록 완료!**\n만료일: `{exp_str}`", ephemeral=True)

@bot.tree.command(name="영수증채널설정", description="[관리자/판매자] 구매 영수증이 출력될 채널을 지정합니다.")
@app_commands.describe(채널="영수증 메시지가 전송될 텍스트 채널")
@admin_or_seller_only()
async def set_receipt_channel(interaction: discord.Interaction, 채널: discord.TextChannel):
    conn = get_conn()
    conn.execute(
        "INSERT INTO guild_settings (guild_id, receipt_channel_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET receipt_channel_id = ?",
        (interaction.guild_id, 채널.id, 채널.id)
    )
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ 구매 영수증 채널이 {채널.mention} (으)로 설정되었습니다.", ephemeral=True)

# ---------------------------------------------------------------------------
# [관리자/판매자] 상품 등록 및 권한 설정
# ---------------------------------------------------------------------------
@bot.tree.command(name="판매자등록", description="[관리자] 특정 유저에게 패널 및 상품 관리 권한을 부여합니다.")
@app_commands.describe(유저="판매자로 등록할 유저 멘션 (@유저)")
async def register_seller(interaction: discord.Interaction, 유저: discord.Member):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 이 명령어는 서버 관리자만 실행할 수 있습니다.", ephemeral=True)
        return

    add_bot_seller(interaction.guild_id, 유저.id, interaction.user.id)
    await interaction.response.send_message(f"✅ {유저.mention}님을 **판매자**로 등록했습니다.", ephemeral=True)

@bot.tree.command(name="일반등록", description="[관리자/판매자] 일반 자판기 상품 등록 및 1회성 재고를 추가합니다.")
@app_commands.describe(상품명="상품 이름", 가격="상품 가격(원)", 재고내용="DM으로 발송될 핀코드/계정 등")
@admin_or_seller_only()
async def add_standard_stock(interaction: discord.Interaction, 상품명: str, 가격: int, 재고내용: str):
    conn = get_conn()
    conn.execute(
        "INSERT INTO prices (guild_id, item, price, target_type, is_permanent) VALUES (?, ?, ?, 'standard', 0) "
        "ON CONFLICT(guild_id, item) DO UPDATE SET price = ?",
        (interaction.guild_id, 상품명, 가격, 가격)
    )
    conn.execute("INSERT INTO item_stocks (guild_id, item, content, is_used) VALUES (?, ?, ?, 0)", (interaction.guild_id, 상품명, 재고내용))
    stock_count = conn.execute("SELECT COUNT(*) as cnt FROM item_stocks WHERE guild_id = ? AND item = ? AND is_used = 0", (interaction.guild_id, 상품명)).fetchone()["cnt"]
    conn.execute("UPDATE prices SET stock = ? WHERE guild_id = ? AND item = ?", (stock_count, interaction.guild_id, 상품명))
    conn.commit()
    conn.close()

    await interaction.response.send_message(f"✅ **[일반]** `{상품명}` (`{fmt_won(가격)}`) 재고가 추가되었습니다. (남은 재고: **{stock_count}개**)", ephemeral=True)

@bot.tree.command(name="커스텀등록", description="[관리자/판매자] 커스텀 자판기 상품(고정 메시지/다운로드 링크)을 등록합니다.")
@app_commands.describe(상품명="상품 이름", 가격="상품 가격(원)", 발송내용="DM으로 발송할 고정 안내문/링크")
@admin_or_seller_only()
async def register_custom(interaction: discord.Interaction, 상품명: str, 가격: int, 발송내용: str):
    conn = get_conn()
    conn.execute(
        "INSERT INTO prices (guild_id, item, price, target_type, is_permanent) VALUES (?, ?, ?, 'custom', 1) "
        "ON CONFLICT(guild_id, item) DO UPDATE SET price = ?, target_type = 'custom', is_permanent = 1",
        (interaction.guild_id, 상품명, 가격, 가격)
    )
    conn.execute(
        "INSERT INTO permanent_stocks (guild_id, item, content) VALUES (?, ?, ?) ON CONFLICT(guild_id, item) DO UPDATE SET content = ?",
        (interaction.guild_id, 상품명, 발송내용, 발송내용)
    )
    conn.commit()
    conn.close()

    await interaction.response.send_message(f"✅ **[커스텀]** `{상품명}` (`{fmt_won(가격)}`) 상품이 등록되었습니다.", ephemeral=True)

@bot.tree.command(name="역할등록", description="[관리자/판매자] 역할 지급 자판기 상품을 등록합니다.")
@app_commands.describe(상품명="상품 이름", 가격="상품 가격(원)", 부여역할="지급할 디스코드 역할")
@admin_or_seller_only()
async def register_role(interaction: discord.Interaction, 상품명: str, 가격: int, 부여역할: discord.Role):
    conn = get_conn()
    conn.execute(
        "INSERT INTO prices (guild_id, item, price, target_type, is_permanent, role_id) VALUES (?, ?, ?, 'role', 1, ?) "
        "ON CONFLICT(guild_id, item) DO UPDATE SET price = ?, target_type = 'role', role_id = ?",
        (interaction.guild_id, 상품명, 가격, 부여역할.id, 가격, 부여역할.id)
    )
    conn.commit()
    conn.close()

    await interaction.response.send_message(f"✅ **[역할]** `{상품명}` (`{fmt_won(가격)}`, 역할: {부여역할.mention}) 상품이 등록되었습니다.", ephemeral=True)

@bot.tree.command(name="수동충전", description="[관리자/판매자] 특정 유저의 포인트를 수동으로 충전해줍니다.")
@app_commands.describe(유저="대상 유저 멘션 (@유저)", 금액="충전할 금액(원)")
@admin_or_seller_only()
async def manual_charge(interaction: discord.Interaction, 유저: discord.Member, 금액: int):
    conn = get_conn()
    conn.execute(
        "INSERT INTO user_points (guild_id, user_id, points) VALUES (?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE SET points = points + ?",
        (interaction.guild_id, 유저.id, 금액, 금액)
    )
    conn.commit()
    conn.close()
    current_total = get_user_points(interaction.guild_id, 유저.id)
    await interaction.response.send_message(f"✅ {유저.mention}님에게 **{fmt_won(금액)}** 수동 충전 완료 (잔액: {fmt_won(current_total)})", ephemeral=True)

# ---------------------------------------------------------------------------
# 유저 정보 조회
# ---------------------------------------------------------------------------
@bot.tree.command(name="포인트조회", description="내 남은 포인트 잔액 및 VIP 등급을 확인합니다.")
async def check_my_points(interaction: discord.Interaction):
    pts = get_user_points(interaction.guild_id, interaction.user.id)
    tier_info = get_user_tier_info(interaction.guild_id, interaction.user.id)
    
    disc_percent = int(tier_info["discount_rate"] * 100)
    status_str = f"{tier_info['icon']} **{tier_info['name']}** ({disc_percent}% 할인 혜택)"

    embed = discord.Embed(
        title="💰 내 포인트 잔액 및 등급 정보",
        description=f"{interaction.user.mention}님의 회원 정보입니다.\n\n"
                    f"• **보유 포인트:** `{fmt_won(pts)}`\n"
                    f"• **회원 등급:** {status_str}\n"
                    f"• **누적 결제:** `{fmt_won(tier_info['total_spent'])}` ({tier_info['count']}회 구매)\n\n"
                    f"💡 {tier_info['next_goal']}",
        color=discord.Color.gold()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="내구매내역", description="최근 구매한 상품 내역을 확인합니다.")
async def check_my_transactions(interaction: discord.Interaction):
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, item, quantity, total_price, created_at FROM transactions WHERE guild_id = ? AND buyer_id = ? ORDER BY id DESC LIMIT 5",
        (interaction.guild_id, interaction.user.id)
    ).fetchall()
    conn.close()

    if not rows:
        await interaction.response.send_message("📦 최근 구매 내역이 없습니다.", ephemeral=True)
        return

    embed = discord.Embed(title="📜 최근 구매 내역 (최근 5건)", color=discord.Color.blurple())
    for r in rows:
        embed.add_field(
            name=f"주문 번호 #{r['id']} - {r['item']} ({r['quantity']}개)",
            value=f"• 결제 금액: `{fmt_won(r['total_price'])}` | 일시: `{r['created_at']}`",
            inline=False
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ---------------------------------------------------------------------------
# 구매 처리 로직
# ---------------------------------------------------------------------------
async def process_purchase(interaction: discord.Interaction, item_name: str, quantity: int, total_price: int, memo_text: str = "자판기 구매"):
    guild_id = interaction.guild_id
    user_id = interaction.user.id

    my_pts = get_user_points(guild_id, user_id)
    if my_pts < total_price:
        await interaction.response.send_message(f"❌ 잔액이 부족합니다! (내 잔액: {fmt_won(my_pts)}, 필요 금액: {fmt_won(total_price)})", ephemeral=True)
        return

    conn = get_conn()
    item_info = conn.execute("SELECT * FROM prices WHERE guild_id = ? AND item = ?", (guild_id, item_name)).fetchone()

    if not item_info:
        conn.close()
        await interaction.response.send_message("⚠️ 존재하지 않는 상품입니다.", ephemeral=True)
        return

    target_type = item_info["target_type"]
    role_id = item_info["role_id"]
    combined_accounts = ""

    if target_type == "standard":
        stock_rows = conn.execute("SELECT * FROM item_stocks WHERE guild_id = ? AND item = ? AND is_used = 0 LIMIT ?", (guild_id, item_name, quantity)).fetchall()
        if len(stock_rows) < quantity:
            conn.close()
            await interaction.response.send_message("❌ 처리 도중 재고가 부족해졌습니다.", ephemeral=True)
            return

        account_contents = []
        for s_row in stock_rows:
            account_contents.append(s_row["content"])
            conn.execute("UPDATE item_stocks SET is_used = 1 WHERE id = ?", (s_row["id"],))

        combined_accounts = "\n---\n".join(account_contents)
        real_stock_count = conn.execute("SELECT COUNT(*) as cnt FROM item_stocks WHERE guild_id = ? AND item = ? AND is_used = 0", (guild_id, item_name)).fetchone()["cnt"]
        conn.execute("UPDATE prices SET stock = ? WHERE guild_id = ? AND item = ?", (real_stock_count, guild_id, item_name))

    elif target_type == "custom":
        perm_row = conn.execute("SELECT content FROM permanent_stocks WHERE guild_id = ? AND item = ?", (guild_id, item_name)).fetchone()
        if not perm_row:
            conn.close()
            await interaction.response.send_message("⚠️ 안내문 내용이 등록되지 않았습니다.", ephemeral=True)
            return
        combined_accounts = perm_row["content"]

    elif target_type == "role":
        role = interaction.guild.get_role(role_id)
        if not role:
            conn.close()
            await interaction.response.send_message("❌ 부여할 역할을 서버에서 찾을 수 없습니다.", ephemeral=True)
            return

        if role in interaction.user.roles:
            conn.close()
            await interaction.response.send_message("⚠️ 이미 가지고 있는 역할입니다.", ephemeral=True)
            return

        try:
            await interaction.user.add_roles(role)
            combined_accounts = f"🎉 {role.name} 역할이 정상적으로 부여되었습니다."
        except discord.Forbidden:
            conn.close()
            await interaction.response.send_message("❌ 권한 오류: 봇 역할의 순위가 지급할 역할보다 위에 있어야 합니다.", ephemeral=True)
            return

    conn.execute("UPDATE user_points SET points = points - ? WHERE guild_id = ? AND user_id = ?", (total_price, guild_id, user_id))
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO transactions (guild_id, buyer_id, buyer_name, item, quantity, unit_price, total_price, memo, created_at, recorded_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'System')",
        (guild_id, user_id, str(interaction.user), item_name, quantity, int(total_price/quantity), total_price, memo_text, now_kst_str())
    )
    tx_id = cur.lastrowid
    setting_row = conn.execute("SELECT receipt_channel_id FROM guild_settings WHERE guild_id = ?", (guild_id,)).fetchone()
    conn.commit()
    conn.close()

    tier_info = get_user_tier_info(guild_id, user_id)
    tier_text = f" ({tier_info['icon']} {tier_info['name']} {int(tier_info['discount_rate']*100)}% 할인 적용)" if tier_info['discount_rate'] > 0 else ""

    receipt_text = (
        f"구매자: {interaction.user.mention}\n"
        f"구매 감사드립니다{tier_text}\n"
        f"💰 {fmt_won(total_price)}\n"
        f"📦 {item_name}\n"
        f"🔢 {quantity}개"
    )

    receipt_embed = discord.Embed(
        title=f"🧾 구매 영수증 [주문 #{tx_id}]",
        description=receipt_text,
        color=discord.Color.green(),
        timestamp=datetime.now(KST)
    )

    dm_success = True
    try:
        dm_embed = discord.Embed(
            title="🎉 [구매 성공] 지급 안내",
            description=f"구매하신 **{item_name}**의 정보입니다:\n\n```text\n{combined_accounts}\n```",
            color=discord.Color.gold()
        )
        await interaction.user.send(embed=dm_embed)
        await interaction.user.send(embed=receipt_embed)
    except Exception:
        dm_success = False

    if setting_row and setting_row["receipt_channel_id"]:
        receipt_channel = interaction.guild.get_channel(setting_row["receipt_channel_id"])
        if receipt_channel:
            try:
                await receipt_channel.send(embed=receipt_embed)
            except Exception:
                pass

    msg = f"✅ **{item_name}** ({quantity}개) 구매 완료!"
    if target_type != "role" and not dm_success:
        msg += f"\n⚠️ **DM 차단 상태**로 지급 실패! 지급 정보:\n`{combined_accounts}`"
    else:
        msg += "\n📬 상세 정보가 DM으로 발송되었습니다."

    await interaction.response.send_message(msg, ephemeral=True)

# ---------------------------------------------------------------------------
# 자판기 패널 UI 및 드롭다운
# ---------------------------------------------------------------------------
class MainVendingView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📦 일반 자판기", style=discord.ButtonStyle.primary, custom_id="btn_standard")
    async def standard_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = get_conn()
        rows = conn.execute("SELECT * FROM prices WHERE guild_id = ? AND target_type = 'standard' AND stock > 0", (interaction.guild_id,)).fetchall()
        conn.close()

        if not rows:
            return await interaction.response.send_message("❌ 구매 가능한 일반 상품(재고 있음)이 없습니다.", ephemeral=True)

        options = [discord.SelectOption(label=r['item'], description=f"가격: {fmt_won(r['price'])} | 재고: {r['stock']}개", value=r['item']) for r in rows]
        view = discord.ui.View()
        view.add_item(VendingItemSelect(options))
        await interaction.response.send_message("📦 구매할 일반 상품을 선택해주세요:", view=view, ephemeral=True)

    @discord.ui.button(label="🎨 커스텀 자판기", style=discord.ButtonStyle.success, custom_id="btn_custom")
    async def custom_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = get_conn()
        rows = conn.execute("SELECT * FROM prices WHERE guild_id = ? AND target_type = 'custom'", (interaction.guild_id,)).fetchall()
        conn.close()

        if not rows:
            return await interaction.response.send_message("❌ 등록된 커스텀 상품이 없습니다.", ephemeral=True)

        options = [discord.SelectOption(label=r['item'], description=f"가격: {fmt_won(r['price'])}", value=r['item']) for r in rows]
        view = discord.ui.View()
        view.add_item(VendingItemSelect(options))
        await interaction.response.send_message("🎨 발송받을 커스텀 상품을 선택해주세요:", view=view, ephemeral=True)

    @discord.ui.button(label="👑 역할 자판기", style=discord.ButtonStyle.secondary, custom_id="btn_role")
    async def role_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = get_conn()
        rows = conn.execute("SELECT * FROM prices WHERE guild_id = ? AND target_type = 'role'", (interaction.guild_id,)).fetchall()
        conn.close()

        if not rows:
            return await interaction.response.send_message("❌ 등록된 역할 상품이 없습니다.", ephemeral=True)

        options = [discord.SelectOption(label=r['item'], description=f"가격: {fmt_won(r['price'])}", value=r['item']) for r in rows]
        view = discord.ui.View()
        view.add_item(VendingItemSelect(options))
        await interaction.response.send_message("👑 획득할 역할 상품을 선택해주세요:", view=view, ephemeral=True)

class VendingItemSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(placeholder="상품을 선택해주세요", options=options, custom_id="select_buy_item")

    async def callback(self, interaction: discord.Interaction):
        item_name = self.values[0]
        await interaction.response.send_modal(QuantityModal(item_name))

class QuantityModal(discord.ui.Modal, title="🧮 수량 선택 및 결제 확인"):
    quantity_input = discord.ui.TextInput(
        label="구매할 개수 (숫자만 입력)",
        placeholder="예: 1",
        default="1",
        min_length=1,
        max_length=5
    )

    def __init__(self, item_name: str):
        super().__init__()
        self.item_name = item_name

    async def on_submit(self, interaction: discord.Interaction):
        raw_val = self.quantity_input.value.strip()
        if not raw_val.isdigit() or int(raw_val) <= 0:
            await interaction.response.send_message("❌ 올바른 숫자를 입력해주세요!", ephemeral=True)
            return

        qty = int(raw_val)
        conn = get_conn()
        item_row = conn.execute("SELECT * FROM prices WHERE guild_id = ? AND item = ?", (interaction.guild_id, self.item_name)).fetchone()
        conn.close()

        if not item_row:
            await interaction.response.send_message("⚠️ 존재하지 않는 상품입니다.", ephemeral=True)
            return

        unit_price = item_row["price"]
        raw_total_price = unit_price * qty
        stock = item_row["stock"]
        target_type = item_row["target_type"]

        if target_type == "standard" and stock < qty:
            await interaction.response.send_message(f"❌ 재고가 부족합니다! (남은 재고: {stock}개)", ephemeral=True)
            return

        tier_info = get_user_tier_info(interaction.guild_id, interaction.user.id)
        discount_rate = tier_info["discount_rate"]
        discount_amount = int(raw_total_price * discount_rate)
        final_total_price = raw_total_price - discount_amount

        desc = (
            f"상품명: **{self.item_name}**\n"
            f"• 구매 수량: **{qty}개**\n"
            f"• 개당 단가: `{fmt_won(unit_price)}`\n"
            f"• 정가 금액: `{fmt_won(raw_total_price)}`\n"
        )

        if discount_rate > 0:
            disc_pct = int(discount_rate * 100)
            desc += (
                f"🎉 {tier_info['icon']} **{tier_info['name']} {disc_pct}% 할인 적용!** (`-{fmt_won(discount_amount)}`)\n"
                f"👉 **최종 결제 금액: `{fmt_won(final_total_price)}`**\n\n"
            )
        else:
            desc += f"👉 **총 예상 가격: `{fmt_won(final_total_price)}`**\n\n"

        desc += "구매를 진행하시겠습니까? 보유 포인트에서 결제됩니다."

        embed = discord.Embed(
            title="🛒 구매 및 결제 금액 확인",
            description=desc,
            color=discord.Color.green()
        )
        view = VendingConfirmView(self.item_name, qty, final_total_price)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class VendingConfirmView(discord.ui.View):
    def __init__(self, item_name: str, quantity: int, total_price: int):
        super().__init__(timeout=60)
        self.item_name = item_name
        self.quantity = quantity
        self.total_price = total_price

    @discord.ui.button(label="✅ 최종 구매 확정", style=discord.ButtonStyle.danger, custom_id="confirm_buy_item")
    async def confirm_purchase(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_purchase(interaction, self.item_name, self.quantity, self.total_price, memo_text="통합 자판기 구매")

# ---------------------------------------------------------------------------
# 티켓 시스템 UI
# ---------------------------------------------------------------------------
class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎫 티켓 열기", style=discord.ButtonStyle.primary, custom_id="open_ticket")
    async def open_ticket_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        user = interaction.user

        conn = get_conn()
        existing = conn.execute("SELECT channel_id FROM ticket_logs WHERE guild_id = ? AND owner_id = ?", (guild.id, user.id)).fetchone()
        if existing:
            ch = guild.get_channel(existing["channel_id"])
            if ch:
                conn.close()
                await interaction.response.send_message(f"⚠️ 이미 생성된 티켓 채널이 있습니다: {ch.mention}", ephemeral=True)
                return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }

        channel_name = f"티켓-{user.name}"
        ticket_channel = await guild.create_text_channel(name=channel_name, overwrites=overwrites, reason=f"{user} 티켓 개설")

        conn.execute("INSERT INTO ticket_logs (channel_id, guild_id, owner_id, opened_at) VALUES (?, ?, ?, ?)", (ticket_channel.id, guild.id, user.id, now_kst_str()))
        conn.commit()
        conn.close()

        embed = discord.Embed(
            title=f"🎫 {user.display_name}님의 티켓",
            description="상담 및 지원 전용 채널입니다.",
            color=discord.Color.blue()
        )
        await ticket_channel.send(content=f"{user.mention} 님, 티켓이 생성되었습니다.", embed=embed, view=TicketControlView())
        await interaction.response.send_message(f"✅ 티켓 채널이 생성되었습니다: {ticket_channel.mention}", ephemeral=True)

class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 티켓 닫기", style=discord.ButtonStyle.danger, custom_id="close_ticket")
    async def close_ticket_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel_id = interaction.channel_id
        conn = get_conn()
        t_row = conn.execute("SELECT * FROM ticket_logs WHERE channel_id = ?", (channel_id,)).fetchone()
        
        owner_id = t_row["owner_id"] if t_row else interaction.user.id
        conn.execute("DELETE FROM ticket_logs WHERE channel_id = ?", (channel_id,))
        conn.commit()
        conn.close()

        guild = interaction.guild
        member = guild.get_member(owner_id)

        await interaction.response.send_message("📢 **티켓이 종료되어 30분 동안 자동 타임아웃이 적용됩니다.**")

        if member:
            try:
                await member.timeout(timedelta(minutes=30), reason="티켓 종료 타임아웃")
            except Exception:
                pass

        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason="티켓 닫기")
        except Exception:
            pass

# ---------------------------------------------------------------------------
# 패널 생성 명령어 (자판기, 티켓, 인증, 동적 알림)
# ---------------------------------------------------------------------------
@bot.tree.command(name="자판기생성", description="[관리자/판매자] 현재 채널에 통합 자판기 메인 패널을 생성합니다.")
@admin_or_seller_only()
async def spawn_vending_machine(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🏪 통합 자판기 센터",
        description=(
            "원하시는 자판기 유형의 버튼을 누른 후, 드롭다운 메뉴에서 상품을 선택해주세요.\n\n"
            "📦 **일반 자판기**: 차감형 상품 (코드, 계정 등)\n"
            "🎨 **커스텀 자판기**: 고정 안내문/다운로드 링크 수령\n"
            "👑 **역할 자판기**: 서버 전용 역할 즉시 획득"
        ),
        color=discord.Color.blue()
    )
    embed.set_footer(text="원활한 수령을 위해 DM 수신 허용 상태를 확인하세요.")
    
    await interaction.channel.send(embed=embed, view=MainVendingView())
    await interaction.response.send_message("✅ 자판기 패널이 성공적으로 생성되었습니다.", ephemeral=True)

@bot.tree.command(name="티켓패널", description="[관리자/판매자] 티켓 패널을 전송합니다.")
@admin_or_seller_only()
async def ticket_panel(interaction: discord.Interaction):
    server_name = interaction.guild.name if interaction.guild else "서버"
    embed = discord.Embed(
        title=f"🎫 {server_name} 문의 및 지원 티켓",
        description="아래 **[🎫 티켓 열기]** 버튼을 눌러 상담 및 문의를 진행하세요.",
        color=discord.Color.green()
    )
    await interaction.channel.send(embed=embed, view=TicketPanelView())
    await interaction.response.send_message("✅ 티켓 패널이 생성되었습니다.", ephemeral=True)

@bot.tree.command(name="인증패널", description="[관리자/판매자] 보안 회원 인증 패널을 전송합니다.")
@admin_or_seller_only()
async def verify_panel(interaction: discord.Interaction):
    server_name = interaction.guild.name if interaction.guild else "서버"
    embed = discord.Embed(
        title=f"🔒 {server_name} 회원 인증",
        description="아래 **[인증하기 🔓]** 버튼을 누른 후, 안내되는 4자리 숫자를 입력해 주세요.",
        color=discord.Color.green()
    )
    await interaction.channel.send(embed=embed, view=VerifyView())
    await interaction.response.send_message("✅ 인증 패널이 생성되었습니다.", ephemeral=True)

# ---------------------------------------------------------------------------
# 동적 알림 패널 관련 명령어
# ---------------------------------------------------------------------------
@bot.tree.command(name="알림패널생성", description="[관리자/판매자] 기본 알림 설정 패널을 생성합니다.")
@admin_or_seller_only()
async def create_notification_panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🔔 알림 역할 설정",
        description="받으실 알림을 눌러주세요\n____________________",
        color=discord.Color.gold()
    )
    view = discord.ui.View(timeout=None)
    
    msg = await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message(
        f"✅ 알림 패널이 생성되었습니다!\n"
        f"👉 **메시지 ID:** `{msg.id}`\n"
        f"이제 `/알림버튼추가` 명령어로 이 패널에 원하는 역할을 연결해주세요.",
        ephemeral=True
    )

@bot.tree.command(name="알림버튼추가", description="[관리자/판매자] 알림 패널에 특정 역할 지급 버튼을 추가합니다.")
@app_commands.describe(메시지id="알림 패널 메시지의 ID", 버튼이름="버튼에 표시될 이름", 지급역할="지급할 역할(@역할)")
@admin_or_seller_only()
async def add_notification_button(interaction: discord.Interaction, 메시지id: str, 버튼이름: str, 지급역할: discord.Role):
    try:
        target_msg = await interaction.channel.fetch_message(int(메시지id))
    except Exception:
        await interaction.response.send_message("❌ 해당 메시지를 찾을 수 없습니다. 패널이 생성된 채널에서 실행해주세요.", ephemeral=True)
        return

    view = discord.ui.View.from_message(target_msg) if target_msg.components else discord.ui.View(timeout=None)
    view.add_item(DynamicNotificationButton(label=버튼이름, role_id=지급역할.id))
    
    await target_msg.edit(view=view)
    await interaction.response.send_message(f"✅ 패널에 **[{버튼이름}]** ({지급역할.mention}) 버튼이 추가되었습니다!", ephemeral=True)

@bot.tree.command(name="핑지우개버튼추가", description="[관리자/판매자] 알림 패널에 모든 알림을 해제하는 핑지우개 버튼을 추가합니다.")
@app_commands.describe(메시지id="알림 패널 메시지의 ID")
@admin_or_seller_only()
async def add_clear_button(interaction: discord.Interaction, 메시지id: str):
    try:
        target_msg = await interaction.channel.fetch_message(int(메시지id))
    except Exception:
        await interaction.response.send_message("❌ 해당 메시지를 찾을 수 없습니다. 패널이 생성된 채널에서 실행해주세요.", ephemeral=True)
        return

    view = discord.ui.View.from_message(target_msg) if target_msg.components else discord.ui.View(timeout=None)
    view.add_item(ClearAllNotificationButton())
    
    await target_msg.edit(view=view)
    await interaction.response.send_message("✅ 패널에 **🧹 핑지우개** 버튼이 추가되었습니다!", ephemeral=True)

# ---------------------------------------------------------------------------
# 봇 실행
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if not TOKEN or TOKEN == "YOUR_BOT_TOKEN_HERE":
        raise SystemExit("❌ DISCORD_TOKEN이 설정되지 않았습니다. .env 환경변수를 설정하거나 토큰을 입력하세요.")
    bot.run(TOKEN)
