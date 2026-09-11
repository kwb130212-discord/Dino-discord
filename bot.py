import os
import sqlite3
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN")
DB_PATH = os.getenv("DINO_DB", "dino.sqlite3")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN environment variable is required")

intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS guilds (guild_id INTEGER PRIMARY KEY, registered_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS log_channels (guild_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL)")


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


async def send_log(guild: discord.Guild, embed: discord.Embed):
    channel_id = log_channel_id(guild.id)
    if not channel_id:
        return
    channel = guild.get_channel(channel_id)
    if channel is None:
        return
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        pass


@bot.event
async def on_ready():
    init_db()
    await bot.tree.sync()
    print(f"Logged in as {bot.user} ({bot.user.id})")


@bot.tree.command(name="등록", description="현재 서버에서 로거봇을 사용하도록 등록합니다.")
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


@bot.tree.command(name="로그채널", description="메시지 삭제/수정 로그를 받을 채널을 설정합니다.")
@app_commands.describe(channel="로그를 받을 채널")
@app_commands.guild_only()
async def set_log_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not is_admin(interaction):
        await interaction.response.send_message("서버 소유자 또는 관리자만 사용할 수 있습니다.", ephemeral=True)
        return
    if not registered(interaction.guild.id):
        await interaction.response.send_message("먼저 /등록을 실행해주세요.", ephemeral=True)
        return
    with db() as conn:
        conn.execute(
            "INSERT INTO log_channels(guild_id, channel_id) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET channel_id = excluded.channel_id",
            (interaction.guild.id, channel.id),
        )
    await interaction.response.send_message(f"✅ 로그 채널을 {channel.mention} 으로 설정했습니다.")


@bot.tree.command(name="유저조회", description="서버 내 Discord 사용자의 공개 프로필 정보를 조회합니다.")
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
