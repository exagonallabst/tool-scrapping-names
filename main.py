#!/usr/bin/env python3


import os
import asyncio
import aiohttp
import random
import string
import io
from typing import List

import discord
from discord import app_commands
from discord.ext import commands


DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_USER_TOKEN = os.getenv("DISCORD_USER_TOKEN")
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "2.0"))

if not DISCORD_BOT_TOKEN or not DISCORD_USER_TOKEN:
    raise RuntimeError("Missing DISCORD_BOT_TOKEN or DISCORD_USER_TOKEN in environment.")

USER_HEADERS = {
    "Authorization": DISCORD_USER_TOKEN,
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}
API_URL = "https://discord.com/api/v9/users/@me"



def gen_numbers(count: int, length: int) -> List[str]:
    return [''.join(random.choices(string.digits, k=length)) for _ in range(count)]

def gen_alnum(count: int, length: int) -> List[str]:
    chars = string.ascii_letters + string.digits
    return [''.join(random.choices(chars, k=length)) for _ in range(count)]

def gen_words(count: int, wordlist: List[str]) -> List[str]:
    if not wordlist:
        return []
    return random.choices(wordlist, k=count)

def gen_pattern(prefix: str, count: int, num_len: int) -> List[str]:
    return [prefix + ''.join(random.choices(string.digits, k=num_len)) for _ in range(count)]



async def check_username(session: aiohttp.ClientSession, username: str) -> bool:
    payload = {"username": username}
    try:
        async with session.patch(API_URL, headers=USER_HEADERS, json=payload) as resp:
            if resp.status == 200:
                return True
            elif resp.status == 400:
                data = await resp.json()
                if "username" in data:
                    for msg in data["username"]:
                        if "taken" in msg.lower() or "unavailable" in msg.lower():
                            return False
                return False
            elif resp.status == 429:
                retry = (await resp.json()).get("retry_after", 5)
                await asyncio.sleep(retry + 1)
                return await check_username(session, username)
            else:
                return False
    except:
        return False

async def check_list(usernames: List[str], delay: float = REQUEST_DELAY) -> List[str]:
    """
    Returns ONLY the available (claimed) usernames.
    Failed ones are ignored and not returned.
    """
    available = []
    async with aiohttp.ClientSession() as session:
        for idx, name in enumerate(usernames, start=1):
            print(f"[{idx}/{len(usernames)}] Checking {name}")
            if await check_username(session, name):
                available.append(name)
            await asyncio.sleep(delay)
    return available



intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("Bot is ready for slash commands.")



async def send_available_only(channel: discord.TextChannel, available: List[str], generator_name: str):
    if not channel.permissions_for(channel.guild.me).send_messages:
        print(f"Cannot send to {channel.name} – missing permissions.")
        return
    if not available:
        embed = discord.Embed(
            title=f"Username Check – {generator_name}",
            description="No available usernames found.",
            color=discord.Color.red()
        )
        await channel.send(embed=embed)
        return

    # Create a txt file with the list
    file_data = "\n".join(available)
    file = discord.File(io.StringIO(file_data), filename=f"available_{generator_name}.txt")
    
    embed = discord.Embed(
        title=f"✅ Available Usernames – {generator_name}",
        description=f"Found {len(available)} available usernames (all claimed).",
        color=discord.Color.green()
    )
    # Show first 10 in embed preview
    preview = "\n".join(available[:10])
    if len(available) > 10:
        preview += f"\n... and {len(available)-10} more (see attached file)"
    embed.add_field(name="Preview", value=f"```\n{preview}```", inline=False)
    await channel.send(embed=embed, file=file)



@bot.tree.command(name="numbers", description="Generate random numeric usernames")
@app_commands.describe(
    count="How many to generate (max 50)",
    length="Length of number string (1-10)",
    check="Check availability and claim if free",
    channel="Text channel to send the report (only available names)"
)
async def slash_numbers(
    interaction: discord.Interaction,
    count: int,
    length: int,
    check: bool = False,
    channel: discord.TextChannel = None
):
    if count > 50: count = 50
    if length < 1: length = 1
    if length > 10: length = 10
    names = gen_numbers(count, length)
    await interaction.response.send_message(
        f"Generated {len(names)} numbers:\n" + "\n".join(names[:20]) +
        (f"\n... and {len(names)-20} more" if len(names) > 20 else "")
    )
    if check:
        await interaction.followup.send("Checking availability... (may take a while)")
        available = await check_list(names)
        target = channel or interaction.channel
        await send_available_only(target, available, "numbers")
        if target != interaction.channel:
            await interaction.followup.send(f"Report (available only) sent to {target.mention}")
    else:
        if channel:
            await interaction.followup.send(f"Report channel set, but check is False. Use check=True to run checks.")

@bot.tree.command(name="alnum", description="Generate random alphanumeric usernames")
@app_commands.describe(
    count="How many to generate (max 50)",
    length="Length of string (3-12)",
    check="Check availability and claim if free",
    channel="Text channel to send the report (only available names)"
)
async def slash_alnum(
    interaction: discord.Interaction,
    count: int,
    length: int,
    check: bool = False,
    channel: discord.TextChannel = None
):
    if count > 50: count = 50
    if length < 3: length = 3
    if length > 12: length = 12
    names = gen_alnum(count, length)
    await interaction.response.send_message(
        f"Generated {len(names)} alphanumeric:\n" + "\n".join(names[:20]) +
        (f"\n... and {len(names)-20} more" if len(names) > 20 else "")
    )
    if check:
        await interaction.followup.send("Checking availability...")
        available = await check_list(names)
        target = channel or interaction.channel
        await send_available_only(target, available, "alnum")
        if target != interaction.channel:
            await interaction.followup.send(f"Report (available only) sent to {target.mention}")
    else:
        if channel:
            await interaction.followup.send(f"Report channel set, but check is False. Use check=True.")

@bot.tree.command(name="words", description="Generate random words from an uploaded wordlist file")
@app_commands.describe(
    file="Upload a .txt file with one word per line",
    count="How many to generate (max 50)",
    check="Check availability and claim if free",
    channel="Text channel to send the report (only available names)"
)
async def slash_words(
    interaction: discord.Interaction,
    file: discord.Attachment,
    count: int,
    check: bool = False,
    channel: discord.TextChannel = None
):
    if count > 50: count = 50
    if not file.filename.endswith(".txt"):
        await interaction.response.send_message("File must be a .txt file.")
        return
    try:
        content = await file.read()
        words = content.decode("utf-8").splitlines()
        words = [w.strip() for w in words if w.strip()]
    except Exception as e:
        await interaction.response.send_message(f"Failed to read file: {e}")
        return
    if not words:
        await interaction.response.send_message("Wordlist is empty.")
        return
    names = gen_words(count, words)
    await interaction.response.send_message(
        f"Generated {len(names)} words from wordlist.\n" + "\n".join(names[:20]) +
        (f"\n... and {len(names)-20} more" if len(names) > 20 else "")
    )
    if check:
        await interaction.followup.send("Checking availability...")
        available = await check_list(names)
        target = channel or interaction.channel
        await send_available_only(target, available, "words")
        if target != interaction.channel:
            await interaction.followup.send(f"Report (available only) sent to {target.mention}")
    else:
        if channel:
            await interaction.followup.send(f"Report channel set, but check is False. Use check=True.")

@bot.tree.command(name="pattern", description="Generate usernames with prefix + random digits")
@app_commands.describe(
    prefix="Fixed prefix string (alphanumeric, no spaces)",
    count="How many to generate (max 50)",
    num_len="Number of random digits after prefix (1-6)",
    check="Check availability and claim if free",
    channel="Text channel to send the report (only available names)"
)
async def slash_pattern(
    interaction: discord.Interaction,
    prefix: str,
    count: int,
    num_len: int,
    check: bool = False,
    channel: discord.TextChannel = None
):
    if count > 50: count = 50
    if num_len < 1: num_len = 1
    if num_len > 6: num_len = 6
    if not prefix.isalnum():
        await interaction.response.send_message("Prefix must be alphanumeric (letters and digits only).")
        return
    names = gen_pattern(prefix, count, num_len)
    await interaction.response.send_message(
        f"Generated {len(names)} patterns:\n" + "\n".join(names[:20]) +
        (f"\n... and {len(names)-20} more" if len(names) > 20 else "")
    )
    if check:
        await interaction.followup.send("Checking availability...")
        available = await check_list(names)
        target = channel or interaction.channel
        await send_available_only(target, available, "pattern")
        if target != interaction.channel:
            await interaction.followup.send(f"Report (available only) sent to {target.mention}")
    else:
        if channel:
            await interaction.followup.send(f"Report channel set, but check is False. Use check=True.")



from aiohttp import web

async def handle(request):
    return web.Response(text="Bot is alive")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=8080)
    await site.start()
    print("Web server started on port 8080")



async def main():
    # Start web server in the background
    asyncio.create_task(start_web_server())
    # Start the Discord bot
    await bot.start(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())