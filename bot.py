import hashlib
import os
import secrets
import sqlite3
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from oauth_server import start_server

TOKEN = os.getenv("DISCORD_TOKEN")
DB_PATH = os.getenv("DINO_DB", "dino.sqlite3")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN environment variable is required")

intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
oauth_runner = None


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS guilds (guild_id INTEGER PRIMARY KEY, registered_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS log_channels (guild_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS recovery_keys ("
            "key_hash TEXT PRIMARY KEY, guild_id INTEGER NOT NULL, key_type TEXT NOT NULL, "
            "created_at TEXT NOT NULL, used_at TEXT, revoked_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS oauth_states ("
            "state_hash TEXT PRIMARY KEY, key_hash TEXT NOT NULL, guild_id INTEGER NOT NULL, "
            "role_id INTEGER NOT NULL, expires_at INTEGER NOT NULL)"
        )


def is_admin(interaction: discord.Interaction) -> bool:
    return bool(interaction.guild and interaction.user.guild_permissions.administrator)


def registered(guild_id: int) -> bool:
    with db() as conn:
        return conn.execute("SELECT 1 FROM guilds WHERE guild_id = ?", (guild_id,)).fetchone() is not None


def log_channel_id(guild_id: int):
    with db() as conn:
        row = conn.execute("SELECT channel_id FROM log_channels WHERE guild_id = ?", (guild_id,)).fetchone()
        return row[0] if row else None


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def get_recovery_key(key_hash: str):
    with db() as conn:
        row = conn.execute(
            "SELECT key_hash, guild_id, key_type, created_at, used_at, revoked_at "
            "FROM recovery_keys WHERE key_hash = ? AND revoked_at IS NULL",
            (key_hash,),
        ).fetchone()
        if row is None or (row["key_type"] == "one_time" and row["used_at"] is not None):
            return None
        return row


def save_oauth_state(state_hash: str, key_hash_value: str, guild_id: int, role_id: int, expires_at: int):
    with db() as conn:
        conn.execute(
            "INSERT INTO oauth_states(state_hash, key_hash, guild_id, role_id, expires_at) VALUES (?, ?, ?, ?, ?)",
            (state_hash, key_hash_value, guild_id, role_id, expires_at),
        )


def consume_oauth_state(state_hash: str):
    with db() as conn:
        row = conn.execute(
            "SELECT state_hash, key_hash, guild_id, role_id, expires_at, "
            "(SELECT key_type FROM recovery_keys WHERE key_hash = oauth_states.key_hash) AS key_type "
            "FROM oauth_states WHERE state_hash = ?",
            (state_hash,),
        ).fetchone()
        if row:
            conn.execute("DELETE FROM oauth_states WHERE state_hash = ?", (state_hash,))
        return row


def consume_recovery_key(key_hash_value: str):
    with db() as conn:
        conn.execute(
            "UPDATE recovery_keys SET used_at = ? WHERE key_hash = ? AND key_type = 'one_time' AND used_at IS NULL",
            (utc_now(), key_hash_value),
        )


def create_recovery_key(guild_id: int, key_type: str):
    prefix = "DINO-PERM" if key_type == "permanent" else "DINO-ONCE"
    key = f"{prefix}-{secrets.token_urlsafe(24)}"
    with db() as conn:
        conn.execute(
            "INSERT INTO recovery_keys(key_hash, guild_id, key_type, created_at) VALUES (?, ?, ?, ?)",
            (hash_key(key), guild_id, key_type, utc_now()),
        )
    return key


bot.get_recovery_key = get_recovery_key
bot.save_oauth_state = save_oauth_state
bot.consume_oauth_state = consume_oauth_state
bot.consume_recovery_key = consume_recovery_key


async def send_log(guild: discord.Guild, embed: discord.Embed):
    channel_id = log_channel_id(guild.id)
    if not channel_id:
        return
    channel = guild.get_channel(channel_id)
    if channel is None:
        return
    try:
        await channel.send(embed=embed)
    except discord.HTTPException as exc:
        print(f"Log send failed in guild {guild.id}: {exc}")


@bot.event
async def on_ready():
    global oauth_runner
    init_db()
    await bot.tree.sync()
    if oauth_runner is None:
        oauth_runner = await start_server(bot)
    print(f"Logged in as {bot.user} ({bot.user.id})")


@bot.tree.command(name="register", description="현재 서버에서 로거봇을 사용하도록 등록합니다.")
@app_commands.guild_only()
async def register(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("서버 소유자 또는 관리자만 사용할 수 있습니다.", ephemeral=True)
        return
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO guilds(guild_id, registered_at) VALUES (?, ?)",
            (interaction.guild.id, utc_now()),
        )
    await interaction.response.send_message("✅ 이 서버가 로거봇 사용 서버로 등록되었습니다.")


@bot.tree.command(name="logchannel", description="메시지 삭제/수정 로그를 받을 채널을 설정합니다.")
@app_commands.describe(channel="로그를 받을 채널")
@app_commands.guild_only()
async def set_log_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not is_admin(interaction):
        await interaction.response.send_message("서버 소유자 또는 관리자만 사용할 수 있습니다.", ephemeral=True)
        return
    if not registered(interaction.guild.id):
        await interaction.response.send_message("먼저 /register을 실행해주세요.", ephemeral=True)
        return
    with db() as conn:
        conn.execute(
            "INSERT INTO log_channels(guild_id, channel_id) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET channel_id = excluded.channel_id",
            (interaction.guild.id, channel.id),
        )
    await interaction.response.send_message(f"✅ 로그 채널을 {channel.mention} 으로 설정했습니다.")


@bot.tree.command(name="recoverykey", description="영구 또는 일회용 서버 복구키를 생성합니다.")
@app_commands.describe(kind="복구키 종류")
@app_commands.choices(kind=[
    app_commands.Choice(name="영구 복구키", value="permanent"),
    app_commands.Choice(name="일회용 복구키", value="one_time"),
])
@app_commands.guild_only()
async def recovery_key(interaction: discord.Interaction, kind: app_commands.Choice[str]):
    if not is_admin(interaction):
        await interaction.response.send_message("서버 소유자 또는 관리자만 사용할 수 있습니다.", ephemeral=True)
        return
    if not registered(interaction.guild.id):
        await interaction.response.send_message("먼저 /register을 실행해주세요.", ephemeral=True)
        return
    key = create_recovery_key(interaction.guild.id, kind.value)
    await interaction.response.send_message(
        f"복구키 생성 완료: **{kind.name}**\n아래 키는 다시 표시되지 않습니다. 안전한 곳에 보관하세요.\n`{key}`",
        ephemeral=True,
    )


@bot.tree.command(name="recoverykeys", description="현재 서버의 복구키 상태를 확인합니다.")
@app_commands.guild_only()
async def recovery_keys(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("서버 소유자 또는 관리자만 사용할 수 있습니다.", ephemeral=True)
        return
    with db() as conn:
        rows = conn.execute(
            "SELECT key_type, created_at, used_at, revoked_at FROM recovery_keys WHERE guild_id = ? ORDER BY created_at DESC",
            (interaction.guild.id,),
        ).fetchall()
    if not rows:
        await interaction.response.send_message("등록된 복구키가 없습니다.", ephemeral=True)
        return
    lines = []
    for row in rows[:20]:
        state = "폐기" if row["revoked_at"] else ("사용됨" if row["used_at"] else "활성")
        label = "영구" if row["key_type"] == "permanent" else "일회용"
        lines.append(f"• {label} / {state} / {row['created_at']}")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@bot.tree.command(name="userlookup", description="서버 내 Discord 사용자의 공개 프로필 정보를 조회합니다.")
@app_commands.describe(user="조회할 Discord 사용자")
@app_commands.guild_only()
async def user_lookup(interaction: discord.Interaction, user: discord.Member):
    if not is_admin(interaction):
        await interaction.response.send_message("서버 소유자 또는 관리자만 사용할 수 있습니다.", ephemeral=True)
        return
    embed = discord.Embed(title="사용자 조회", timestamp=datetime.now(timezone.utc))
    embed.add_field(name="사용자", value=f"{user} ({user.mention})", inline=False)
    embed.add_field(name="Discord ID", value=str(user.id), inline=True)
    embed.add_field(name="서버 가입일", value=discord.utils.format_dt(user.joined_at, "F") if user.joined_at else "알 수 없음", inline=True)
    embed.add_field(name="계정 생성일", value=discord.utils.format_dt(user.created_at, "F"), inline=True)
    embed.add_field(name="봇 여부", value="예" if user.bot else "아니오", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.event
async def on_message_delete(message: discord.Message):
    if not message.guild or not registered(message.guild.id) or not message.author:
        return
    embed = discord.Embed(title="🗑️ 메시지 삭제", timestamp=datetime.now(timezone.utc))
    embed.add_field(name="작성자", value=f"{message.author} ({message.author.id})", inline=False)
    embed.add_field(name="채널", value=message.channel.mention, inline=True)
    content = message.content or "(텍스트 없음)"
    embed.add_field(name="내용", value=content[:1024], inline=False)
    await send_log(message.guild, embed)


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if not before.guild or before.author.bot or not registered(before.guild.id):
        return
    if before.content == after.content:
        return
    embed = discord.Embed(title="✏️ 메시지 수정", timestamp=datetime.now(timezone.utc))
    embed.add_field(name="작성자", value=f"{before.author} ({before.author.id})", inline=False)
    embed.add_field(name="채널", value=before.channel.mention, inline=True)
    embed.add_field(name="수정 전", value=(before.content or "(텍스트 없음)")[:1024], inline=False)
    embed.add_field(name="수정 후", value=(after.content or "(텍스트 없음)")[:1024], inline=False)
    await send_log(before.guild, embed)


init_db()
bot.run(TOKEN)
