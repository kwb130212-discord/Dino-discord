import json
import os
from datetime import datetime, timezone

import aiohttp
import discord
import feedparser
from discord import app_commands
from discord.ext import commands, tasks

SETTINGS_FILE = "data/youtube_subscriptions.json"
CHECK_INTERVAL = 10


def load_subs():
    if not os.path.exists(SETTINGS_FILE):
        return {}
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"YouTube 설정 로드 실패: {exc}")
        return {}


def save_subs(data):
    os.makedirs("data", exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_rss_url(channel_id):
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


class YouTube(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.subs = load_subs()
        self.check_youtube.start()

    def cog_unload(self):
        self.check_youtube.cancel()

    @tasks.loop(minutes=CHECK_INTERVAL)
    async def check_youtube(self):
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for guild_id, channels in list(self.subs.items()):
                guild = self.bot.get_guild(int(guild_id))
                if not guild:
                    continue
                for yt_channel_id, info in list(channels.items()):
                    try:
                        async with session.get(get_rss_url(yt_channel_id)) as resp:
                            if resp.status != 200:
                                print(f"YouTube RSS HTTP {resp.status}: {yt_channel_id}")
                                continue
                            text = await resp.text()
                        feed = feedparser.parse(text)
                        if not feed.entries:
                            continue
                        latest = feed.entries[0]
                        latest_id = latest.get("yt_videoid", "")
                        if latest_id and latest_id != info.get("last_video_id"):
                            self.subs[guild_id][yt_channel_id]["last_video_id"] = latest_id
                            save_subs(self.subs)
                            channel = guild.get_channel(int(info["discord_channel_id"]))
                            if channel:
                                await self.send_notification(channel, latest, info["name"])
                    except (aiohttp.ClientError, ValueError, KeyError) as exc:
                        print(f"YouTube 체크 오류 ({yt_channel_id}): {exc}")

    @check_youtube.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    async def send_notification(self, channel, entry, yt_name):
        video_id = entry.get("yt_videoid", "")
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        thumbnail = entry.get("media_thumbnail", [{}])[0].get("url", "")
        published = entry.get("published", "")
        summary = entry.get("summary", "")
        embed = discord.Embed(title=entry.get("title", "새 영상"), url=video_url, description=(summary[:300] + "..." if len(summary) > 300 else summary), color=discord.Color.red(), timestamp=datetime.now(timezone.utc))
        embed.set_author(name=f"📺 {yt_name} - 새 영상 업로드!")
        if thumbnail:
            embed.set_image(url=thumbnail)
        embed.set_footer(text=f"게시일: {published[:10] if published else '알 수 없음'}")
        await channel.send(content=f"🔔 **{yt_name}** 새 영상이 올라왔어요!\n{video_url}", embed=embed)

    @app_commands.command(name="유튜브구독", description="유튜브 채널 새 영상 알림을 설정합니다.")
    @app_commands.describe(채널id="유튜브 채널 ID (UCxxxxxxxx 형식)", 채널이름="표시할 채널 이름", 알림채널="알림을 보낼 디스코드 채널")
    @app_commands.checks.has_permissions(administrator=True)
    async def subscribe_youtube(self, interaction, 채널id: str, 채널이름: str, 알림채널: discord.TextChannel):
        if not 채널id.startswith("UC"):
            await interaction.response.send_message("유튜브 채널 ID는 `UC...` 형식이어야 해요.", ephemeral=True)
            return
        guild_id = str(interaction.guild.id)
        last_id = ""
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(get_rss_url(채널id)) as resp:
                    if resp.status != 200:
                        await interaction.response.send_message(f"유튜브 RSS를 가져오지 못했어요. HTTP {resp.status}", ephemeral=True)
                        return
                    feed = feedparser.parse(await resp.text())
            if feed.entries:
                last_id = feed.entries[0].get("yt_videoid", "")
        except (aiohttp.ClientError, ValueError) as exc:
            await interaction.response.send_message(f"유튜브 채널 확인에 실패했어요: {exc}", ephemeral=True)
            return
        self.subs.setdefault(guild_id, {})[채널id] = {"name": 채널이름, "discord_channel_id": str(알림채널.id), "last_video_id": last_id}
        save_subs(self.subs)
        await interaction.response.send_message(f"✅ **{채널이름}** 구독 완료!\n새 영상은 {알림채널.mention} 에 알림이 가요.\n체크 주기: 매 {CHECK_INTERVAL}분", ephemeral=True)

    @app_commands.command(name="유튜브목록", description="구독 중인 유튜브 채널 목록을 봅니다.")
    async def list_youtube(self, interaction):
        subs = self.subs.get(str(interaction.guild.id), {})
        if not subs:
            await interaction.response.send_message("구독 중인 채널이 없어요.", ephemeral=True)
            return
        embed = discord.Embed(title="📺 구독 중인 유튜브 채널", color=discord.Color.red())
        for yt_id, info in subs.items():
            channel = interaction.guild.get_channel(int(info["discord_channel_id"]))
            embed.add_field(name=info["name"], value=f"ID: `{yt_id}`\n알림채널: {channel.mention if channel else '삭제됨'}", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="유튜브삭제", description="유튜브 알림 구독을 해제합니다.")
    @app_commands.describe(채널id="삭제할 유튜브 채널 ID")
    @app_commands.checks.has_permissions(administrator=True)
    async def unsubscribe_youtube(self, interaction, 채널id: str):
        guild_id = str(interaction.guild.id)
        subs = self.subs.get(guild_id, {})
        if 채널id not in subs:
            await interaction.response.send_message("해당 채널을 찾을 수 없어요.", ephemeral=True)
            return
        name = subs[채널id]["name"]
        del subs[채널id]
        save_subs(self.subs)
        await interaction.response.send_message(f"✅ **{name}** 구독을 해제했어요.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(YouTube(bot))
