import discord
from discord.ext import commands, tasks
import os
import asyncio
from dotenv import load_dotenv

load_dotenv()

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

COGS = [
    "cogs.verification",
    "cogs.logs",
    "cogs.protection",
    "cogs.youtube",
    "cogs.clan_game",
    "cogs.moderation",
]

@bot.event
async def on_ready():
    print(f"✅ {bot.user} 온라인! ({len(bot.guilds)}개 서버)")
    await bot.tree.sync()
    print("📡 슬래시 커맨드 동기화 완료")
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="클랜 서버 관리중 🛡️"
        )
    )

async def main():
    async with bot:
        for cog in COGS:
            try:
                await bot.load_extension(cog)
                print(f"  ✔ {cog} 로드됨")
            except Exception as e:
                print(f"  ✘ {cog} 로드 실패: {e}")
        await bot.start(os.getenv("DISCORD_TOKEN"))

if __name__ == "__main__":
    asyncio.run(main())
