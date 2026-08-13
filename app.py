# -*- coding: utf-8 -*-
import os
import json
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
intents.guilds = True


class GatedCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id is None:
            await interaction.response.send_message("이 봇은 서버 안에서만 사용할 수 있어요.", ephemeral=False)
            return False

        if is_user_blacklisted(interaction.guild_id, interaction.user.id):
            await interaction.response.send_message("❌ 이 서버에서 차단(블랙리스트)된 유저입니다. 봇을 이용할 수 없습니다.", ephemeral=False)
            return False

        data = getattr(interaction, "data", None) or {}
        cmd_name = data.get("name") if isinstance(data, dict) else getattr(data, "name", None)

        public_cmds = [
            "라이센스등록", "발로란트전적", "포인트조회", "내구매내역",
            "랭킹", "구매하기", "판매하기", "일정목록", "유저정보"
        ]
        if cmd_name in public_cmds:
            return True

        if not is_guild_registered(interaction.guild_id):
            await interaction.response.send_message(
                "⚠️ 이 서버는 사용 승인이 되지 않았거나 라이센스가 만료되었습니다.\n"
                "- 봇 개발자의 직인 승인(`!서버등록`) 또는 `/라이센스등록` 명령어를 이용해주세요.",
                ephemeral=False,
            )
            return False

        custom_id = None
        if isinstance(data, dict):
            custom_id = data.get("custom_id")
        else:
            custom_id = getattr(data, "custom_id", None)

        allowed_custom_ids = [
            "btn_buy_standard", "btn_buy_custom", "btn_buy_role",
            "btn_sell_standard", "btn_sell_custom", "btn_sell_role",
            "vending_buy", "vending_products", "vending_charge", "vending_info",
            "select_category", "select_buy_item", "confirm_buy_item",
            "open_ticket", "close_ticket", "ticket_buy", "select_ticket_item",
            "verify_button", "backup_restore_confirm", "backup_restore_cancel"
        ]

        if custom_id:
            if custom_id in allowed_custom_ids or custom_id.startswith("notif_role_") or custom_id.startswith("mod_kick_") or custom_id.startswith("mod_ban_"):
                return True

        if interaction.type == discord.InteractionType.application_command:
            if not is_admin_or_seller(interaction):
                await interaction.response.send_message(
                    "❌ 이 기능은 관리자 또는 등록된 판매자만 사용할 수 있어요.",
                    ephemeral=False,
                )
                return False

        return True


bot = commands.Bot(command_prefix="!", intents=intents, tree_cls=GatedCommandTree)


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
            welcome_channel_id INTEGER,
            audit_channel_id INTEGER,
            min_account_days INTEGER DEFAULT 0,
            msg_log_channel_id INTEGER,
            audit_log_channel_id INTEGER,
            verify_log_channel_id INTEGER
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
        CREATE TABLE IF NOT EXISTS blacklists (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            reason TEXT,
            registered_at TEXT NOT NULL,
            PRIMARY KEY (guild_id, user_id)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            schedule_date TEXT NOT NULL,
            description TEXT,
            created_by INTEGER NOT NULL
        )
    """)
    # 💾 서버 백업 정보 테이블 (역할 + 채널 구조 + 권한까지 저장)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS server_backups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            backup_name TEXT NOT NULL,
            backup_data TEXT NOT NULL,
            created_by INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    try:
        cur.execute("ALTER TABLE prices ADD COLUMN role_id INTEGER DEFAULT NULL")
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()


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


def is_user_blacklisted(guild_id: int, user_id: int) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM blacklists WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)).fetchone()
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
        await interaction.response.send_message("❌ 이 명령어는 관리자 또는 등록된 판매자만 사용할 수 있어요.", ephemeral=False)
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
    bot.add_view(BuyVendingView())
    bot.add_view(SellVendingView())
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
# 📝 로그 이벤트 리스너
# ---------------------------------------------------------------------------
@bot.event
async def on_message_delete(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    conn = get_conn()
    setting = conn.execute("SELECT msg_log_channel_id FROM guild_settings WHERE guild_id = ?", (message.guild.id,)).fetchone()
    conn.close()

    if setting and setting["msg_log_channel_id"]:
        ch = message.guild.get_channel(setting["msg_log_channel_id"])
        if ch:
            embed = discord.Embed(title="🗑️ [메시지 삭제됨]", color=discord.Color.red(), timestamp=datetime.now(KST))
            embed.add_field(name="작성자", value=message.author.mention, inline=True)
            embed.add_field(name="채널", value=message.channel.mention, inline=True)
            embed.add_field(name="내용", value=message.content or "(내용 없음 / 임베드 또는 파일)", inline=False)
            try:
                await ch.send(embed=embed)
            except Exception:
                pass


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if before.author.bot or not before.guild or before.content == after.content:
        return
    conn = get_conn()
    setting = conn.execute("SELECT msg_log_channel_id FROM guild_settings WHERE guild_id = ?", (before.guild.id,)).fetchone()
    conn.close()

    if setting and setting["msg_log_channel_id"]:
        ch = before.guild.get_channel(setting["msg_log_channel_id"])
        if ch:
            embed = discord.Embed(title="✏️ [메시지 수정됨]", color=discord.Color.orange(), timestamp=datetime.now(KST))
            embed.add_field(name="작성자", value=before.author.mention, inline=True)
            embed.add_field(name="채널", value=before.channel.mention, inline=True)
            embed.add_field(name="수정 전", value=before.content or "(내용 없음)", inline=False)
            embed.add_field(name="수정 후", value=after.content or "(내용 없음)", inline=False)
            try:
                await ch.send(embed=embed)
            except Exception:
                pass


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if before.guild.id != after.guild.id:
        return

    conn = get_conn()
    setting = conn.execute("SELECT audit_log_channel_id FROM guild_settings WHERE guild_id = ?", (before.guild.id,)).fetchone()
    conn.close()

    if not setting or not setting["audit_log_channel_id"]:
        return

    ch = before.guild.get_channel(setting["audit_log_channel_id"])
    if not ch:
        return

    before_roles = set(before.roles)
    after_roles = set(after.roles)
    added_roles = after_roles - before_roles

    for r in added_roles:
        if r.name == VERIFY_ROLE_NAME:
            v_conn = get_conn()
            v_setting = v_conn.execute("SELECT verify_log_channel_id FROM guild_settings WHERE guild_id = ?", (before.guild.id,)).fetchone()
            v_conn.close()
            if v_setting and v_setting["verify_log_channel_id"]:
                v_ch = before.guild.get_channel(v_setting["verify_log_channel_id"])
                if v_ch:
                    embed = discord.Embed(
                        title="🔓 [회원 인증 완료]",
                        description=f"유저: {after.mention}\n가입일: `{after.joined_at.strftime('%Y-%m-%d %H:%M:%S') if after.joined_at else '알 수 없음'}`",
                        color=discord.Color.green(),
                        timestamp=datetime.now(KST)
                    )
                    try:
                        await v_ch.send(embed=embed)
                    except Exception:
                        pass

    if before.nick != after.nick:
        embed = discord.Embed(title="📝 [닉네임 변경]", color=discord.Color.blue(), timestamp=datetime.now(KST))
        embed.add_field(name="유저", value=after.mention, inline=False)
        embed.add_field(name="변경 전", value=before.nick or before.name, inline=True)
        embed.add_field(name="변경 후", value=after.nick or after.name, inline=True)
        try:
            await ch.send(embed=embed)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 💾 서버 백업 / 복원 시스템
#
# ⚠️ 참고: 이 기능은 "역할·채널 구조·권한 설정"만 백업/복원합니다.
# 멤버 목록이나 유저 계정 자체를 옮기지는 않습니다 (디스코드 정책상 불가).
# 멤버는 복원된 서버의 초대 링크를 통해 각자 다시 들어와야 합니다.
# ---------------------------------------------------------------------------

def _serialize_overwrites(overwrites: dict) -> list:
    """채널의 권한 오버라이트를 role 이름 기준으로 직렬화"""
    result = []
    for target, ow in overwrites.items():
        if not isinstance(target, discord.Role):
            continue  # 유저별 권한은 백업 대상에서 제외 (역할 기반만 복원)
        allow, deny = ow.pair()
        result.append({
            "role_name": target.name,
            "allow": allow.value,
            "deny": deny.value,
        })
    return result


async def build_backup_data(guild: discord.Guild) -> dict:
    roles_data = []
    for role in sorted(guild.roles, key=lambda r: r.position):
        if role.is_default():
            continue
        if role.managed:
            continue  # 봇 전용/부스트 등 관리형 역할은 자동 재생성 불가하므로 스킵
        roles_data.append({
            "name": role.name,
            "color": role.color.value,
            "hoist": role.hoist,
            "mentionable": role.mentionable,
            "permissions": role.permissions.value,
            "position": role.position,
        })

    categories_data = []
    for cat in guild.categories:
        categories_data.append({
            "name": cat.name,
            "position": cat.position,
            "overwrites": _serialize_overwrites(cat.overwrites),
        })

    channels_data = []
    for ch in guild.channels:
        if isinstance(ch, discord.CategoryChannel):
            continue
        ch_type = "text"
        extra = {}
        if isinstance(ch, discord.TextChannel):
            ch_type = "text"
            extra["topic"] = ch.topic
            extra["nsfw"] = ch.nsfw
            extra["slowmode_delay"] = ch.slowmode_delay
        elif isinstance(ch, discord.VoiceChannel):
            ch_type = "voice"
            extra["user_limit"] = ch.user_limit
            extra["bitrate"] = ch.bitrate
        elif isinstance(ch, discord.ForumChannel):
            ch_type = "forum"
            extra["topic"] = ch.topic
        else:
            continue

        channels_data.append({
            "name": ch.name,
            "type": ch_type,
            "position": ch.position,
            "category": ch.category.name if ch.category else None,
            "overwrites": _serialize_overwrites(ch.overwrites),
            "extra": extra,
        })

    return {
        "guild_name": guild.name,
        "roles": roles_data,
        "categories": categories_data,
        "channels": channels_data,
    }


async def restore_backup_data(guild: discord.Guild, data: dict, progress_cb=None) -> dict:
    """백업 데이터를 현재 guild에 재생성. 이미 존재하는 동일 이름 역할/채널은 건너뜀."""
    created_roles = 0
    created_categories = 0
    created_channels = 0
    role_map = {}  # name -> discord.Role

    existing_role_names = {r.name: r for r in guild.roles}

    # 1) 역할 생성 (백업 시 저장된 순서 = position 낮은 것부터, 즉 아래쪽부터 생성)
    sorted_roles = sorted(data.get("roles", []), key=lambda r: r["position"])
    for r in sorted_roles:
        if r["name"] in existing_role_names:
            role_map[r["name"]] = existing_role_names[r["name"]]
            continue
        try:
            new_role = await guild.create_role(
                name=r["name"],
                colour=discord.Colour(r["color"]),
                hoist=r["hoist"],
                mentionable=r["mentionable"],
                permissions=discord.Permissions(r["permissions"]),
                reason="서버 백업 복원",
            )
            role_map[r["name"]] = new_role
            created_roles += 1
            if progress_cb:
                await progress_cb(f"역할 생성: {r['name']}")
        except discord.Forbidden:
            pass
        await asyncio.sleep(0.3)  # 레이트리밋 방지

    def build_overwrites(ow_list):
        result = {}
        for ow in ow_list:
            role = role_map.get(ow["role_name"]) or discord.utils.get(guild.roles, name=ow["role_name"])
            if not role:
                continue
            result[role] = discord.PermissionOverwrite.from_pair(
                discord.Permissions(ow["allow"]),
                discord.Permissions(ow["deny"]),
            )
        return result

    # 2) 카테고리 생성
    existing_cat_names = {c.name: c for c in guild.categories}
    category_map = {}
    for cat in sorted(data.get("categories", []), key=lambda c: c["position"]):
        if cat["name"] in existing_cat_names:
            category_map[cat["name"]] = existing_cat_names[cat["name"]]
            continue
        try:
            new_cat = await guild.create_category(
                name=cat["name"],
                overwrites=build_overwrites(cat["overwrites"]),
                reason="서버 백업 복원",
            )
            category_map[cat["name"]] = new_cat
            created_categories += 1
            if progress_cb:
                await progress_cb(f"카테고리 생성: {cat['name']}")
        except discord.Forbidden:
            pass
        await asyncio.sleep(0.3)

    # 3) 채널 생성
    existing_ch_names = {c.name for c in guild.channels if not isinstance(c, discord.CategoryChannel)}
    for ch in sorted(data.get("channels", []), key=lambda c: c["position"]):
        if ch["name"] in existing_ch_names:
            continue
        parent = category_map.get(ch["category"]) if ch["category"] else None
        overwrites = build_overwrites(ch["overwrites"])
        extra = ch.get("extra", {})
        try:
            if ch["type"] == "text":
                await guild.create_text_channel(
                    name=ch["name"], category=parent, overwrites=overwrites,
                    topic=extra.get("topic"), nsfw=extra.get("nsfw", False),
                    slowmode_delay=extra.get("slowmode_delay", 0),
                    reason="서버 백업 복원",
                )
            elif ch["type"] == "voice":
                await guild.create_voice_channel(
                    name=ch["name"], category=parent, overwrites=overwrites,
                    user_limit=extra.get("user_limit", 0), bitrate=extra.get("bitrate", 64000),
                    reason="서버 백업 복원",
                )
            elif ch["type"] == "forum":
                await guild.create_forum(
                    name=ch["name"], category=parent, overwrites=overwrites,
                    topic=extra.get("topic"),
                    reason="서버 백업 복원",
                )
            created_channels += 1
            if progress_cb:
                await progress_cb(f"채널 생성: {ch['name']}")
        except discord.Forbidden:
            pass
        except Exception:
            pass
        await asyncio.sleep(0.5)

    return {
        "roles": created_roles,
        "categories": created_categories,
        "channels": created_channels,
    }


@bot.tree.command(name="백업", description="[관리자/판매자] 현재 서버의 역할·채널 구조와 권한을 백업합니다.")
@app_commands.describe(백업이름="백업 파일의 이름")
@admin_or_seller_only()
async def backup_server(interaction: discord.Interaction, 백업이름: str):
    await interaction.response.defer(ephemeral=False)
    guild = interaction.guild
    data = await build_backup_data(guild)
    data_str = json.dumps(data, ensure_ascii=False)

    conn = get_conn()
    conn.execute(
        "INSERT INTO server_backups (guild_id, backup_name, backup_data, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
        (guild.id, 백업이름, data_str, interaction.user.id, now_kst_str())
    )
    conn.commit()
    conn.close()

    embed = discord.Embed(
        title="💾 서버 백업 완료",
        description=(
            f"백업명: **{백업이름}**\n"
            f"• 역할: {len(data['roles'])}개\n"
            f"• 카테고리: {len(data['categories'])}개\n"
            f"• 채널: {len(data['channels'])}개\n\n"
            f"`/백업복원`으로 이 서버 또는 봇이 있는 다른 서버에 그대로 재생성할 수 있습니다.\n"
            f"⚠️ 멤버 목록은 백업되지 않습니다. 멤버는 초대 링크로 각자 재입장해야 합니다."
        ),
        color=discord.Color.blue()
    )
    await interaction.followup.send(embed=embed, ephemeral=False)


@bot.tree.command(name="백업목록", description="[관리자/판매자] 이 서버에 저장된 백업 목록을 확인합니다.")
@admin_or_seller_only()
async def list_backups(interaction: discord.Interaction):
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, backup_name, created_at FROM server_backups WHERE guild_id = ? ORDER BY id DESC LIMIT 15",
        (interaction.guild_id,)
    ).fetchall()
    conn.close()

    if not rows:
        return await interaction.response.send_message("📦 저장된 백업이 없습니다. `/백업`으로 먼저 생성해주세요.", ephemeral=False)

    embed = discord.Embed(title="💾 저장된 백업 목록", color=discord.Color.blue())
    for r in rows:
        embed.add_field(name=f"#{r['id']} — {r['backup_name']}", value=f"생성일: `{r['created_at']}`", inline=False)
    embed.set_footer(text="복원하려면 /백업복원 명령어에 백업ID를 입력하세요.")
    await interaction.response.send_message(embed=embed, ephemeral=False)


class BackupRestoreConfirmView(discord.ui.View):
    def __init__(self, backup_id: int, backup_name: str, data: dict, requester_id: int):
        super().__init__(timeout=60)
        self.backup_id = backup_id
        self.backup_name = backup_name
        self.data = data
        self.requester_id = requester_id

    @discord.ui.button(label="✅ 이 서버에 복원 진행", style=discord.ButtonStyle.danger, custom_id="backup_restore_confirm")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.requester_id:
            return await interaction.response.send_message("❌ 백업 복원을 요청한 본인만 확정할 수 있습니다.", ephemeral=False)

        await interaction.response.send_message("⏳ 복원을 시작합니다. 채널/역할이 많으면 시간이 걸릴 수 있어요...", ephemeral=False)

        async def progress(msg):
            try:
                await interaction.channel.send(f"　└ {msg}")
            except Exception:
                pass

        result = await restore_backup_data(interaction.guild, self.data, progress_cb=progress)

        embed = discord.Embed(
            title="✅ 서버 복원 완료",
            description=(
                f"백업명: **{self.backup_name}**\n"
                f"• 새로 생성된 역할: {result['roles']}개\n"
                f"• 새로 생성된 카테고리: {result['categories']}개\n"
                f"• 새로 생성된 채널: {result['channels']}개\n\n"
                f"이미 같은 이름이 존재하는 항목은 건너뛰었습니다.\n"
                f"멤버들에게는 이 서버 초대 링크를 공지해 재입장을 안내해주세요."
            ),
            color=discord.Color.green()
        )
        await interaction.channel.send(embed=embed)
        self.stop()

    @discord.ui.button(label="❌ 취소", style=discord.ButtonStyle.secondary, custom_id="backup_restore_cancel")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.requester_id:
            return await interaction.response.send_message("❌ 본인만 취소할 수 있습니다.", ephemeral=False)
        await interaction.response.send_message("🚫 복원이 취소되었습니다.", ephemeral=False)
        self.stop()


@bot.tree.command(name="백업복원", description="[관리자/판매자] 저장된 백업을 현재 서버에 복원합니다. (역할/채널/권한만 재생성)")
@app_commands.describe(백업id="/백업목록에서 확인한 백업 ID")
@admin_or_seller_only()
async def restore_server(interaction: discord.Interaction, 백업id: int):
    conn = get_conn()
    row = conn.execute("SELECT * FROM server_backups WHERE id = ?", (백업id,)).fetchone()
    conn.close()

    if not row:
        return await interaction.response.send_message("❌ 해당 ID의 백업을 찾을 수 없습니다.", ephemeral=False)

    data = json.loads(row["backup_data"])

    embed = discord.Embed(
        title="⚠️ 서버 복원 확인",
        description=(
            f"백업명: **{row['backup_name']}** (원본 서버: `{data.get('guild_name', '알 수 없음')}`)\n"
            f"• 역할 {len(data.get('roles', []))}개\n"
            f"• 카테고리 {len(data.get('categories', []))}개\n"
            f"• 채널 {len(data.get('channels', []))}개\n\n"
            f"이 내용을 **현재 서버 ( {interaction.guild.name} )** 에 재생성합니다.\n"
            f"⚠️ 멤버 계정 자체는 옮겨지지 않으며, 이미 존재하는 동일 이름 역할/채널은 건너뜁니다.\n\n"
            f"진행하시겠습니까?"
        ),
        color=discord.Color.orange()
    )
    view = BackupRestoreConfirmView(백업id, row["backup_name"], data, interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=False)


@bot.tree.command(name="백업삭제", description="[관리자/판매자] 저장된 백업을 삭제합니다.")
@app_commands.describe(백업id="/백업목록에서 확인한 백업 ID")
@admin_or_seller_only()
async def delete_backup(interaction: discord.Interaction, 백업id: int):
    conn = get_conn()
    row = conn.execute("SELECT backup_name FROM server_backups WHERE id = ? AND guild_id = ?", (백업id, interaction.guild_id)).fetchone()
    if not row:
        conn.close()
        return await interaction.response.send_message("❌ 해당 ID의 백업을 찾을 수 없습니다.", ephemeral=False)
    conn.execute("DELETE FROM server_backups WHERE id = ?", (백업id,))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"🗑️ 백업 **{row['backup_name']}** (#{백업id})이(가) 삭제되었습니다.", ephemeral=False)


# ---------------------------------------------------------------------------
# 🛠️ 로그 채널 설정 및 자동 제작 명령어
# ---------------------------------------------------------------------------
@bot.tree.command(name="메시지로거", description="[관리자/판매자] 메시지 삭제/수정 로그 채널 지정")
@app_commands.describe(채널="지정할 텍스트 채널")
@admin_or_seller_only()
async def set_msg_log_channel(interaction: discord.Interaction, 채널: discord.TextChannel):
    conn = get_conn()
    conn.execute("INSERT INTO guild_settings (guild_id, msg_log_channel_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET msg_log_channel_id = ?", (interaction.guild_id, 채널.id, 채널.id))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ 메시지 로거 채널이 {채널.mention}(으)로 설정되었습니다.", ephemeral=False)


@bot.tree.command(name="감사로그", description="[관리자/판매자] 감사 로그 채널 지정")
@app_commands.describe(채널="지정할 텍스트 채널")
@admin_or_seller_only()
async def set_audit_log_channel(interaction: discord.Interaction, 채널: discord.TextChannel):
    conn = get_conn()
    conn.execute("INSERT INTO guild_settings (guild_id, audit_log_channel_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET audit_log_channel_id = ?", (interaction.guild_id, 채널.id, 채널.id))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ 감사 로그 채널이 {채널.mention}(으)로 설정되었습니다.", ephemeral=False)


@bot.tree.command(name="인증로그", description="[관리자/판매자] 인증 로그 채널 지정")
@app_commands.describe(채널="지정할 텍스트 채널")
@admin_or_seller_only()
async def set_verify_log_channel(interaction: discord.Interaction, 채널: discord.TextChannel):
    conn = get_conn()
    conn.execute("INSERT INTO guild_settings (guild_id, verify_log_channel_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET verify_log_channel_id = ?", (interaction.guild_id, 채널.id, 채널.id))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ 인증 로그 채널이 {채널.mention}(으)로 설정되었습니다.", ephemeral=False)


@bot.tree.command(name="메시지로그채널제작", description="[관리자/판매자] 메시지 로그 채널 자동 생성")
@admin_or_seller_only()
async def create_msg_log_channel(interaction: discord.Interaction):
    guild = interaction.guild
    overwrites = {guild.default_role: discord.PermissionOverwrite(read_messages=False), guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)}
    ch = await guild.create_text_channel(name="메시지-로그", overwrites=overwrites)
    conn = get_conn()
    conn.execute("INSERT INTO guild_settings (guild_id, msg_log_channel_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET msg_log_channel_id = ?", (guild.id, ch.id, ch.id))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ 메시지 로그 채널 생성 완료: {ch.mention}", ephemeral=False)


@bot.tree.command(name="감사로그채널제작", description="[관리자/판매자] 감사 로그 채널 자동 생성")
@admin_or_seller_only()
async def create_audit_log_channel(interaction: discord.Interaction):
    guild = interaction.guild
    overwrites = {guild.default_role: discord.PermissionOverwrite(read_messages=False), guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)}
    ch = await guild.create_text_channel(name="감사-로그", overwrites=overwrites)
    conn = get_conn()
    conn.execute("INSERT INTO guild_settings (guild_id, audit_log_channel_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET audit_log_channel_id = ?", (guild.id, ch.id, ch.id))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ 감사 로그 채널 생성 완료: {ch.mention}", ephemeral=False)


@bot.tree.command(name="인증로그채널제작", description="[관리자/판매자] 인증 로그 채널 자동 생성")
@admin_or_seller_only()
async def create_verify_log_channel(interaction: discord.Interaction):
    guild = interaction.guild
    overwrites = {guild.default_role: discord.PermissionOverwrite(read_messages=False), guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)}
    ch = await guild.create_text_channel(name="인증-로그", overwrites=overwrites)
    conn = get_conn()
    conn.execute("INSERT INTO guild_settings (guild_id, verify_log_channel_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET verify_log_channel_id = ?", (guild.id, ch.id, ch.id))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"✅ 인증 로그 채널 생성 완료: {ch.mention}", ephemeral=False)


@bot.tree.command(name="유저정보", description="특정 유저의 상세 정보(가입일 등)를 조회합니다.")
@app_commands.describe(유저="조회할 유저 멘션")
async def user_info(interaction: discord.Interaction, 유저: discord.Member = None):
    target = 유저 or interaction.user
    created_at = target.created_at.strftime("%Y-%m-%d %H:%M:%S")
    joined_at = target.joined_at.strftime("%Y-%m-%d %H:%M:%S") if target.joined_at else "알 수 없음"

    embed = discord.Embed(title=f"👤 유저 상세 정보 — {target.display_name}", color=discord.Color.blurple())
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="디스코드 태그", value=f"`{target}`", inline=True)
    embed.add_field(name="유저 ID", value=f"`{target.id}`", inline=True)
    embed.add_field(name="계정 생성일", value=f"`{created_at}`", inline=False)
    embed.add_field(name="서버 가입일", value=f"`{joined_at}`", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=False)


# ---------------------------------------------------------------------------
# 기본 자판기 / 티켓 / 인증 뷰
# ---------------------------------------------------------------------------
class BuyVendingView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📦 상품 구매", style=discord.ButtonStyle.primary, custom_id="btn_buy_standard")
    async def btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("자판기 패널을 이용해주세요.", ephemeral=True)


class SellVendingView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)


class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)


class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="인증하기 🔓", style=discord.ButtonStyle.green, custom_id="verify_button")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("인증 버튼 클릭됨", ephemeral=True)


if __name__ == "__main__":
    if not TOKEN or TOKEN == "YOUR_BOT_TOKEN_HERE":
        raise SystemExit("❌ DISCORD_TOKEN이 설정되지 않았습니다.")
    bot.run(TOKEN)
