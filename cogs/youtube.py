import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import feedparser
import json
import os
from datetime import datetime, timezone

SETTINGS_FILE = "data/youtube_subscriptions.json"
CHECK_INTERVAL = 10  # 분 단위로 체크

def load_subs():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    return {}

def save_subs(data):
    os.makedirs("data", exist_ok=True)
    with open(SETTINGS_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def get_rss_url(channel_id: str) -> str:
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


class YouTube(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.subs = load_subs()   # { guild_id: { channel_id: { name, discord_channel_id, last_video_id } } }
        self.check_youtube.start()

    def cog_unload(self):
        self.check_youtube.cancel()

    @tasks.loop(minutes=CHECK_INTERVAL)
    async def check_youtube(self):
        for guild_id, channels in self.subs.items():
            guild = self.bot.get_guild(int(guild_id))
            if not guild:
                continue

            for yt_channel_id, info in channels.items():
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(get_rss_url(yt_channel_id)) as resp:
                            text = await resp.text()

                    feed = feedparser.parse(text)
                    if not feed.entries:
                        continue

                    latest = feed.entries[0]
                    latest_id = latest.get("yt_videoid", "")

                    if latest_id and latest_id != info.get("last_video_id"):
                        # 새 영상 감지
                        self.subs[guild_id][yt_channel_id]["last_video_id"] = latest_id
                        save_subs(self.subs)

                        discord_ch = guild.get_channel(int(info["discord_channel_id"]))
                        if discord_ch:
                            await self.send_notification(discord_ch, latest, info["name"])

                except Exception as e:
                    print(f"YouTube 체크 오류 ({yt_channel_id}): {e}")

    @check_youtube.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    async def send_notification(self, channel, entry, yt_name: str):
        video_url = f"https://www.youtube.com/watch?v={entry.get('yt_videoid', '')}"
        thumbnail = entry.get("media_thumbnail", [{}])[0].get("url", "")
        published = entry.get("published", "")

        embed = discord.Embed(
            title=entry.get("title", "새 영상"),
            url=video_url,
            description=entry.get("summary", "")[:300] + "..." if len(entry.get("summary", "")) > 300 else entry.get("summary", ""),
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_author(name=f"📺 {yt_name} - 새 영상 업로드!")
        if thumbnail:
            embed.set_image(url=thumbnail)
        embed.set_footer(text=f"게시일: {published[:10] if published else '알 수 없음'}")

        await channel.send(
            content=f"🔔 **{yt_name}** 새 영상이 올라왔어요!\n{video_url}",
            embed=embed
        )

    # ── 슬래시 커맨드 ──────────────────────────────────
    @app_commands.command(name="유튜브구독", description="유튜브 채널 새 영상 알림을 설정합니다.")
    @app_commands.describe(
        채널id="유튜브 채널 ID (UCxxxxxxxx 형식)",
        채널이름="표시할 채널 이름",
        알림채널="새 영상 알림을 보낼 디스코드 채널"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def subscribe_youtube(self, interaction: discord.Interaction,
                                 채널id: str,
                                 채널이름: str,
                                 알림채널: discord.TextChannel):
        guild_id = str(interaction.guild.id)
        if guild_id not in self.subs:
            self.subs[guild_id] = {}

        # 현재 최신 영상 ID 저장 (중복 알림 방지)
        last_id = ""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(get_rss_url(채널id)) as resp:
                    text = await resp.text()
            feed = feedparser.parse(text)
            if feed.entries:
                last_id = feed.entries[0].get("yt_videoid", "")
        except:
            pass

        self.subs[guild_id][채널id] = {
            "name": 채널이름,
            "discord_channel_id": str(알림채널.id),
            "last_video_id": last_id
        }
        save_subs(self.subs)

        await interaction.response.send_message(
            f"✅ **{채널이름}** 구독 완료!\n"
            f"새 영상은 {알림채널.mention} 에 알림이 가요.\n"
            f"체크 주기: 매 {CHECK_INTERVAL}분",
            ephemeral=True
        )

    @app_commands.command(name="유튜브목록", description="구독 중인 유튜브 채널 목록을 봅니다.")
    async def list_youtube(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)
        subs = self.subs.get(guild_id, {})

        if not subs:
            await interaction.response.send_message("구독 중인 채널이 없어요.", ephemeral=True)
            return

        embed = discord.Embed(title="📺 구독 중인 유튜브 채널", color=discord.Color.red())
        for yt_id, info in subs.items():
            discord_ch = interaction.guild.get_channel(int(info["discord_channel_id"]))
            embed.add_field(
                name=info["name"],
                value=f"ID: `{yt_id}`\n알림채널: {discord_ch.mention if discord_ch else '삭제됨'}",
                inline=True
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="유튜브삭제", description="유튜브 알림 구독을 해제합니다.")
    @app_commands.describe(채널id="삭제할 유튜브 채널 ID")
    @app_commands.checks.has_permissions(administrator=True)
    async def unsubscribe_youtube(self, interaction: discord.Interaction, 채널id: str):
        guild_id = str(interaction.guild.id)
        if guild_id in self.subs and 채널id in self.subs[guild_id]:
            name = self.subs[guild_id][채널id]["name"]
            del self.subs[guild_id][채널id]
            save_subs(self.subs)
            await interaction.response.send_message(f"✅ **{name}** 구독을 해제했어요.", ephemeral=True)
        else:
            await interaction.response.send_message("해당 채널을 찾을 수 없어요.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(YouTube(bot))
