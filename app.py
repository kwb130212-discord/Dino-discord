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

TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_ROLE_NAME = os.getenv("ADMIN_ROLE_NAME", "! !디노")
DB_PATH = os.getenv("DB_PATH", "shop.db")
VERIFY_ROLE_NAME = os.getenv("VERIFY_ROLE_NAME", "인증유저")
KST = timezone(timedelta(hours=9))

intents = discord.Intents.default()
intents.members = True          
intents.message_content = True  

# ---------------------------------------------------------------------------
# 디스코드 커맨드 트리 및 권한 체크
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

        if interaction.data and interaction.data.get("custom_id") in [
            "vending_buy", "vending_products", "vending_charge", "vending_info",
            "select_category", "select_buy_item", "confirm_buy_item",
            "open_ticket", "close_ticket", "ticket_buy", "select_ticket_item",
            "verify_button"
        ]:
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
            price INTEGER NOT NULL,
            stock INTEGER DEFAULT -1,
            min_quantity INTEGER DEFAULT 1,
            target_type TEXT DEFAULT 'vending',
            is_permanent INTEGER DEFAULT 0,
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

    # 마이그레이션 처리
    try:
        cur.execute("ALTER TABLE registered_guilds ADD COLUMN expires_at TEXT")
    except sqlite3.OperationalError:
        pass

    try:
        cur.execute("ALTER TABLE prices ADD COLUMN target_type TEXT DEFAULT 'vending'")
    except sqlite3.OperationalError:
        pass

    try:
        cur.execute("ALTER TABLE prices ADD COLUMN is_permanent INTEGER DEFAULT 0")
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
    bot.add_view(VendingMainView())
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
# [랜덤 숫자 보안 인증 UI 및 뷰]
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
                        f"✅ **인증 완료!** `{VERIFY_ROLE_NAME}` 역할을 받으셨습니다. 환영합니다!", 
                        ephemeral=True
                    )
                except discord.Forbidden:
                    await interaction.response.send_message(
                        "⚠️ 봇의 권한이 부족하여 역할을 부여할 수 없습니다.", 
                        ephemeral=True
                    )
            else:
                await interaction.response.send_message(
                    f"⚠️ 서버에 `{VERIFY_ROLE_NAME}` 역할이 존재하지 않습니다.", 
                    ephemeral=True
                )
        else:
            await interaction.response.send_message(
                f"❌ **인증 실패!** 입력하신 숫자(`{typed_val}`)가 일치하지 않습니다.", 
                ephemeral=True
            )

class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="인증하기 🔓", style=discord.ButtonStyle.green, custom_id="verify_button")
    async def verify_button_click(self, interaction: discord.Interaction, button: discord.ui.Button):
        random_code = random.randint(1000, 9999)
        await interaction.response.send_modal(VerifyModal(random_code))

# ---------------------------------------------------------------------------
# 개발자 승인 및 라이센스 관리 명령어
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
# 관리자 및 상품 관리 명령어 (고정형 / 소모형 구분)
# ---------------------------------------------------------------------------
@bot.tree.command(name="판매자등록", description="[관리자] 특정 유저에게 패널 및 상품 관리 권한을 부여합니다.")
@app_commands.describe(유저="판매자로 등록할 유저 멘션 (@유저)")
async def register_seller(interaction: discord.Interaction, 유저: discord.Member):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 이 명령어는 서버 관리자만 실행할 수 있습니다.", ephemeral=True)
        return

    add_bot_seller(interaction.guild_id, 유저.id, interaction.user.id)
    await interaction.response.send_message(f"✅ {유저.mention}님을 **판매자**로 등록했습니다.", ephemeral=True)

@bot.tree.command(name="가격추가", description="[관리자/판매자] 상품을 등록합니다. (고정형: 프로그램 링크 등, 소모형: 계정/코드 등)")
@app_commands.describe(
    상품명="상품 이름", 
    가격="가격(원)", 
    카테고리="카테고리 이름", 
    구분="자판기 상품 또는 티켓 전용 상품",
    고정여부="고정형(프로그램/링크/소모X) 또는 소모형(계정/차감O)"
)
@app_commands.choices(
    구분=[
        app_commands.Choice(name="자판기", value="vending"),
        app_commands.Choice(name="티켓", value="ticket")
    ],
    고정여부=[
        app_commands.Choice(name="고정형 (링크/프로그램/소모X)", value=1),
        app_commands.Choice(name="소모형 (계정/코드/차감O)", value=0)
    ]
)
@admin_or_seller_only()
async def add_price(
    interaction: discord.Interaction, 
    상품명: str, 
    가격: int, 
    카테고리: str = "기타", 
    구분: app_commands.Choice[str] = None,
    고정여부: app_commands.Choice[int] = None
):
    target_type = 구분.value if 구분 else "vending"
    is_perm = 고정여부.value if 고정여부 else 0
    
    type_kor = "자판기" if target_type == "vending" else "티켓"
    perm_kor = "고정형 (무제한)" if is_perm == 1 else "소모형 (개별차감)"

    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO prices (guild_id, item, category, price, stock, target_type, is_permanent) VALUES (?, ?, ?, ?, ?, ?, ?)", 
            (interaction.guild_id, 상품명, 카테고리, 가격, -1 if is_perm else 0, target_type, is_perm)
        )
        conn.commit()
        await interaction.response.send_message(
            f"✅ **[{type_kor} / {카테고리}]** ➡️ **{상품명}** 상품 등록 완료!\n"
            f"• 가격: `{fmt_won(가격)}` | 유형: **{perm_kor}**", 
            ephemeral=True
        )
    except sqlite3.IntegrityError:
        await interaction.response.send_message(f"⚠️ **{상품명}** 상품은 이미 존재합니다.", ephemeral=True)
    finally:
        conn.close()

@bot.tree.command(name="고정재고등록", description="[고정형 전용] 매크로/프로그램 링크 등 차감되지 않는 지급 내용을 등록합니다.")
@app_commands.describe(상품명="등록된 고정형 상품 이름", 지급내용="구매 유저에게 지급될 다운로드 링크/설명문 등")
@admin_or_seller_only()
async def add_permanent_stock(interaction: discord.Interaction, 상품명: str, 지급내용: str):
    conn = get_conn()
    item_row = conn.execute("SELECT * FROM prices WHERE guild_id = ? AND item = ?", (interaction.guild_id, 상품명)).fetchone()
    
    if not item_row:
        conn.close()
        await interaction.response.send_message(f"⚠️ **{상품명}** 상품이 존재하지 않습니다.", ephemeral=True)
        return

    if item_row["is_permanent"] != 1:
        conn.close()
        await interaction.response.send_message(f"⚠️ **{상품명}** 은(는) 고정형 상품이 아닙니다. `/재고등록`을 사용해주세요.", ephemeral=True)
        return

    conn.execute(
        "INSERT INTO permanent_stocks (guild_id, item, content) VALUES (?, ?, ?) ON CONFLICT(guild_id, item) DO UPDATE SET content = ?",
        (interaction.guild_id, 상품명, 지급내용, 지급내용)
    )
    conn.commit()
    conn.close()

    await interaction.response.send_message(f"✅ **[{상품명}]** 고정 상품 지급 내용 등록/수정 완료!", ephemeral=True)

@bot.tree.command(name="재고등록", description="[소모형 전용] 계정/코드/핀번호 등 1회성 재고를 추가합니다.")
@app_commands.describe(상품명="등록된 소모형 상품 이름", 계정정보="지급될 텍스트 (예: id:a\npw:b)")
@admin_or_seller_only()
async def add_item_stock(interaction: discord.Interaction, 상품명: str, 계정정보: str):
    conn = get_conn()
    item_row = conn.execute("SELECT * FROM prices WHERE guild_id = ? AND item = ?", (interaction.guild_id, 상품명)).fetchone()
    
    if not item_row:
        conn.close()
        await interaction.response.send_message(f"⚠️ **{상품명}** 상품이 먼저 `/가격추가`로 등록되어 있어야 합니다.", ephemeral=True)
        return

    if item_row["is_permanent"] == 1:
        conn.close()
        await interaction.response.send_message(f"⚠️ **{상품명}** 은(는) 고정형 상품입니다. `/고정재고등록`을 이용해주세요.", ephemeral=True)
        return

    conn.execute("INSERT INTO item_stocks (guild_id, item, content, is_used) VALUES (?, ?, ?, 0)", (interaction.guild_id, 상품명, 계정정보))
    stock_count = conn.execute("SELECT COUNT(*) as cnt FROM item_stocks WHERE guild_id = ? AND item = ? AND is_used = 0", (interaction.guild_id, 상품명)).fetchone()["cnt"]
    conn.execute("UPDATE prices SET stock = ? WHERE guild_id = ? AND item = ?", (stock_count, interaction.guild_id, 상품명))
    conn.commit()
    conn.close()

    await interaction.response.send_message(f"✅ **{상품명}** 소모형 재고 등록 완료 (가용 재고: {stock_count}개)", ephemeral=True)

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
# 유저 조회 명령어
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
# 핵심 구매 처리 로직 (고정형 vs 소모형 자동 분기)
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

    is_permanent = (item_info["is_permanent"] == 1)
    combined_accounts = ""

    if is_permanent:
        # 📌 고정형 상품 처리 (프로그램 링크 등)
        perm_row = conn.execute("SELECT content FROM permanent_stocks WHERE guild_id = ? AND item = ?", (guild_id, item_name)).fetchone()
        if not perm_row:
            conn.close()
            await interaction.response.send_message("⚠️ 상품의 다운로드 링크/지급 내용이 아직 등록되지 않았습니다. 관리자에게 문의하세요.", ephemeral=True)
            return
        
        combined_accounts = perm_row["content"]
    else:
        # 📌 소모형 상품 처리 (계정, 핀번호 등)
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

        # 소모형 상품 재고 동기화
        real_stock_count = conn.execute("SELECT COUNT(*) as cnt FROM item_stocks WHERE guild_id = ? AND item = ? AND is_used = 0", (guild_id, item_name)).fetchone()["cnt"]
        conn.execute("UPDATE prices SET stock = ? WHERE guild_id = ? AND item = ?", (real_stock_count, guild_id, item_name))

    # 포인트 차감
    conn.execute("UPDATE user_points SET points = points - ? WHERE guild_id = ? AND user_id = ?", (total_price, guild_id, user_id))

    # 거래 기록 저장
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

    # DM 발송
    dm_success = True
    try:
        dm_embed = discord.Embed(
            title="🎉 [구매 성공] 상품 지급 안내",
            description=f"구매하신 상품 **{item_name}**의 정보입니다:\n\n```text\n{combined_accounts}\n```",
            color=discord.Color.gold()
        )
        await interaction.user.send(embed=dm_embed)
        await interaction.user.send(embed=receipt_embed)
    except Exception:
        dm_success = False

    # 서버 영수증 채널 발송
    if setting_row and setting_row["receipt_channel_id"]:
        receipt_channel = interaction.guild.get_channel(setting_row["receipt_channel_id"])
        if receipt_channel:
            try:
                await receipt_channel.send(embed=receipt_embed)
            except Exception as e:
                print(f"영수증 채널 전송 실패: {e}")

    msg = f"✅ **{item_name}** ({quantity}개) 구매가 완료되었습니다!"
    if not dm_success:
        msg += f"\n⚠️ **DM 차단 상태**로 전송 실패! 상품 정보:\n`{combined_accounts}`"
    else:
        msg += "\n📬 개인 메시지(DM)로 상품 안내 정보가 발송되었습니다."

    await interaction.response.send_message(msg, ephemeral=True)

# ---------------------------------------------------------------------------
# 자판기 UI
# ---------------------------------------------------------------------------
class VendingMainView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🛒 구매", style=discord.ButtonStyle.primary, custom_id="vending_buy")
    async def buy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = get_conn()
        rows = conn.execute("SELECT DISTINCT category FROM prices WHERE guild_id = ? AND (target_type = 'vending' OR target_type IS NULL)", (interaction.guild_id,)).fetchall()
        conn.close()

        if not rows:
            await interaction.response.send_message("⚠️ 등록된 카테고리가 없습니다.", ephemeral=True)
            return

        options = [discord.SelectOption(label=r["category"], value=r["category"]) for r in rows]
        view = discord.ui.View()
        view.add_item(CategorySelect(options))
        await interaction.response.send_message("📂 **카테고리를 선택하세요**", view=view, ephemeral=True)

    @discord.ui.button(label="🔍 제품 목록", style=discord.ButtonStyle.secondary, custom_id="vending_products")
    async def products_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = get_conn()
        rows = conn.execute("SELECT * FROM prices WHERE guild_id = ? AND (target_type = 'vending' OR target_type IS NULL) ORDER BY category, item", (interaction.guild_id,)).fetchall()
        conn.close()

        if not rows:
            await interaction.response.send_message("등록된 상품이 없어요.", ephemeral=True)
            return

        embed = discord.Embed(title="🛒 자판기 상품 목록", color=discord.Color.blurple())
        current_cat, desc = "", ""
        for r in rows:
            if current_cat != r["category"]:
                if current_cat != "":
                    embed.add_field(name=f"📁 {current_cat}", value=desc, inline=False)
                    desc = ""
                current_cat = r["category"]

            # 고정형 상품과 소모형 상품 재고 표시 구분
            if r["is_permanent"] == 1:
                stock_txt = "∞ (무제한/고정)"
            else:
                stock_txt = "무제한" if r["stock"] == -1 else f"{r['stock']}개"

            desc += f"• **{r['item']}** - 가격: `{fmt_won(r['price'])}` (재고: {stock_txt})\n"
            
        if current_cat != "":
            embed.add_field(name=f"📁 {current_cat}", value=desc, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="🎁 충전", style=discord.ButtonStyle.success, custom_id="vending_charge")
    async def charge_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        current_pts = get_user_points(interaction.guild_id, interaction.user.id)
        embed = discord.Embed(
            title="💳 포인트 충전 안내",
            description=f"현재 보유 잔액: **{fmt_won(current_pts)}**\n\n- 관리자 또는 판매자에게 문의하여 충전을 진행해주세요.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="⚙️ 내 정보", style=discord.ButtonStyle.secondary, custom_id="vending_info")
    async def info_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        current_pts = get_user_points(interaction.guild_id, interaction.user.id)
        tier_info = get_user_tier_info(interaction.guild_id, interaction.user.id)
        embed = discord.Embed(
            title="🤖 자판기 이용 정보",
            description=f"내 잔액: **{fmt_won(current_pts)}**\n"
                        f"내 등급: {tier_info['icon']} **{tier_info['name']}** ({int(tier_info['discount_rate']*100)}% 할인)\n\n"
                        f"- 상품 구매 시 DM으로 즉시 발송됩니다.",
            color=discord.Color.blurple()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

class CategorySelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(placeholder="카테고리를 선택하세요", options=options, custom_id="select_category")

    async def callback(self, interaction: discord.Interaction):
        selected_category = self.values[0]
        conn = get_conn()
        rows = conn.execute(
            "SELECT * FROM prices WHERE guild_id = ? AND category = ? AND (target_type = 'vending' OR target_type IS NULL) AND (stock > 0 OR stock = -1 OR is_permanent = 1)", 
            (interaction.guild_id, selected_category)
        ).fetchall()
        conn.close()

        if not rows:
            await interaction.response.send_message(f"⚠️ **{selected_category}** 카테고리에 구매 가능한 상품이 없습니다.", ephemeral=True)
            return

        options = [discord.SelectOption(label=r['item'], description=f"단가: {fmt_won(r['price'])}", value=r['item']) for r in rows]
        view = discord.ui.View()
        view.add_item(VendingItemSelect(options))
        await interaction.response.edit_message(content=f"📂 선택된 카테고리: **{selected_category}**\n👇 구매할 상품을 선택해주세요:", view=view)

class VendingItemSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(placeholder="구매할 상품을 선택하세요", options=options, custom_id="select_buy_item")

    async def callback(self, interaction: discord.Interaction):
        item_name = self.values[0]
        await interaction.response.send_modal(QuantityModal(item_name))

class QuantityModal(discord.ui.Modal, title="🧮 수량 선택 및 가격 계산기"):
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
        is_permanent = (item_row["is_permanent"] == 1)

        # 소모형일 경우만 재고 확인
        if not is_permanent and stock != -1 and stock < qty:
            await interaction.response.send_message(f"❌ 재고가 부족합니다! (남은 재고: {stock}개)", ephemeral=True)
            return

        tier_info = get_user_tier_info(interaction.guild_id, interaction.user.id)
        discount_rate = tier_info["discount_rate"]
        discount_amount = int(raw_total_price * discount_rate)
        final_total_price = raw_total_price - discount_amount

        desc = (
            f"상품명: **{self.item_name}** {'[고정형]' if is_permanent else ''}\n"
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
            desc += (
                f"👉 **총 예상 가격: `{fmt_won(final_total_price)}`**\n\n"
            )

        desc += "구매를 진행하시겠습니까? 잔액 차감 후 DM으로 정보가 발송됩니다."

        embed = discord.Embed(
            title="🛒 구매 및 가격 계산 결과",
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
        await process_purchase(interaction, self.item_name, self.quantity, self.total_price, memo_text="자판기 구매")

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
            description="상담 및 티켓 전용 상품 구매 채널입니다.",
            color=discord.Color.blue()
        )
        await ticket_channel.send(content=f"{user.mention} 님, 티켓이 생성되었습니다.", embed=embed, view=TicketControlView())
        await interaction.response.send_message(f"✅ 티켓 채널이 생성되었습니다: {ticket_channel.mention}", ephemeral=True)

class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🛒 티켓 상품 구매", style=discord.ButtonStyle.success, custom_id="ticket_buy")
    async def ticket_buy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = get_conn()
        rows = conn.execute("SELECT * FROM prices WHERE guild_id = ? AND target_type = 'ticket' AND (stock > 0 OR stock = -1 OR is_permanent = 1)", (interaction.guild_id,)).fetchall()
        conn.close()

        if not rows:
            await interaction.response.send_message("⚠️ 현재 구매 가능한 티켓 상품이 없습니다.", ephemeral=True)
            return

        options = [discord.SelectOption(label=r['item'], description=f"가격: {fmt_won(r['price'])}", value=r['item']) for r in rows]
        view = discord.ui.View()
        view.add_item(TicketItemSelect(options))
        await interaction.response.send_message("🎫 **티켓 전용 상품을 선택하세요:**", view=view, ephemeral=True)

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

class TicketItemSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(placeholder="구매할 티켓 상품 선택", options=options, custom_id="select_ticket_item")

    async def callback(self, interaction: discord.Interaction):
        item_name = self.values[0]
        await interaction.response.send_modal(QuantityModal(item_name))

# ---------------------------------------------------------------------------
# 패널 생성 명령어
# ---------------------------------------------------------------------------
@bot.tree.command(name="자판기패널", description="[관리자/판매자] 자판기 메인 패널을 전송합니다.")
@admin_or_seller_only()
async def vending_panel(interaction: discord.Interaction):
    server_name = interaction.guild.name if interaction.guild else "서버"
    embed = discord.Embed(
        title=f"🛒 {server_name} 자판기",
        description="상품 구매 시 다이렉트 메시지(DM)가 허용되어 있어야 합니다.",
        color=discord.Color.blurple()
    )
    view = VendingMainView()
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("✅ 자판기 패널이 생성되었습니다.", ephemeral=True)

@bot.tree.command(name="티켓패널", description="[관리자/판매자] 티켓 패널을 전송합니다.")
@admin_or_seller_only()
async def ticket_panel(interaction: discord.Interaction):
    server_name = interaction.guild.name if interaction.guild else "서버"
    embed = discord.Embed(
        title=f"🎫 {server_name} 문의 및 지원 티켓",
        description="아래 **[🎫 티켓 열기]** 버튼을 눌러 상담 및 구매를 진행하세요.",
        color=discord.Color.green()
    )
    view = TicketPanelView()
    await interaction.channel.send(embed=embed, view=view)
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

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("❌ DISCORD_TOKEN이 설정되지 않았어요.")
    bot.run(TOKEN)
