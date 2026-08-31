#!/usr/bin/env python3
"""
Discord bot – verificador de disponibilidad con endpoint público.
Solo usa GET /users/username?username=... 
- 404 → nombre libre (disponible)
- 200 → nombre ocupado (existe)
- 429 → rate limit, reintenta
- otros → ocupado por seguridad
Variables: DISCORD_BOT_TOKEN (obligatorio), REQUEST_DELAY (opcional)
NO necesita DISCORD_USER_TOKEN.
"""

import os
import asyncio
import aiohttp
import random
import string
import io
import json
import re
from typing import List

import discord
from discord import app_commands
from discord.ext import commands

# ===== CONFIGURACIÓN =====
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "2.0"))

if not DISCORD_BOT_TOKEN:
    raise RuntimeError("Falta DISCORD_BOT_TOKEN en variables de entorno.")

PUBLIC_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
API_BASE = "https://discord.com/api/v9"

# ------------------------------------------------------------------
#  GENERADORES
# ------------------------------------------------------------------

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

# ------------------------------------------------------------------
#  VERIFICADOR PÚBLICO (CORRECTO)
# ------------------------------------------------------------------

async def check_username_available(session: aiohttp.ClientSession, username: str) -> bool:
    """
    Usa GET /users/username?username=... (público).
    Retorna True si el nombre está disponible (404), False si ocupado (200) o error.
    """
    url = f"{API_BASE}/users/username?username={username}"
    try:
        async with session.get(url, headers=PUBLIC_HEADERS) as resp:
            status = resp.status
            text = await resp.text()
            print(f"  GET {username} → status {status}, respuesta: {text[:200]}")
            if status == 404:
                print(f"  → ✅ DISPONIBLE (no existe usuario)")
                return True
            elif status == 200:
                print(f"  → ❌ OCUPADO (existe usuario)")
                return False
            elif status == 429:
                retry = (await resp.json()).get("retry_after", 5)
                print(f"  ⏳ rate limit, esperando {retry}s")
                await asyncio.sleep(retry + 1)
                return await check_username_available(session, username)
            else:
                print(f"  → ❌ ERROR (status {status}), tratado como ocupado")
                return False
    except Exception as e:
        print(f"  → ❌ EXCEPCIÓN: {e}")
        return False

async def check_list(usernames: List[str], delay: float = REQUEST_DELAY) -> List[str]:
    available = []
    async with aiohttp.ClientSession() as session:
        total = len(usernames)
        for idx, name in enumerate(usernames, start=1):
            name = name.strip()
            if not name:
                continue
            if len(name) < 2 or len(name) > 32 or not re.match(r'^[a-zA-Z0-9_]+$', name):
                print(f"  {name} → nombre inválido (longitud o caracteres)")
                continue
            print(f"[{idx}/{total}] Verificando: {name}")
            if await check_username_available(session, name):
                available.append(name)
            await asyncio.sleep(delay)
    return available

# ------------------------------------------------------------------
#  BOT
# ------------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Bot conectado como {bot.user}")
    print("✅ Usando endpoint público /users/username (404 = disponible)")

# ------------------------------------------------------------------
#  ENVÍO DE REPORTE
# ------------------------------------------------------------------

async def send_available_only(channel: discord.TextChannel, available: List[str], generator_name: str):
    if not channel.permissions_for(channel.guild.me).send_messages:
        print(f"Sin permisos para enviar a {channel.name}")
        return
    if not available:
        embed = discord.Embed(
            title=f"Verificación – {generator_name}",
            description="No se encontraron nombres disponibles.",
            color=discord.Color.red()
        )
        await channel.send(embed=embed)
        return

    file_data = "\n".join(available)
    file = discord.File(io.StringIO(file_data), filename=f"available_{generator_name}.txt")
    embed = discord.Embed(
        title=f"✅ Nombres disponibles – {generator_name}",
        description=f"Se encontraron {len(available)} nombres disponibles (ver archivo adjunto).",
        color=discord.Color.green()
    )
    preview = "\n".join(available[:10])
    if len(available) > 10:
        preview += f"\n... y {len(available)-10} más"
    embed.add_field(name="Vista previa", value=f"```\n{preview}```", inline=False)
    await channel.send(embed=embed, file=file)

# ------------------------------------------------------------------
#  COMANDO /test (depuración)
# ------------------------------------------------------------------

@bot.tree.command(name="test", description="Prueba un nombre y muestra la respuesta cruda de la API pública")
@app_commands.describe(username="Nombre a verificar")
async def slash_test(interaction: discord.Interaction, username: str):
    username = username.strip()
    if len(username) < 2 or len(username) > 32 or not re.match(r'^[a-zA-Z0-9_]+$', username):
        await interaction.response.send_message("❌ Nombre inválido: debe tener 2-32 caracteres alfanuméricos o guión bajo.")
        return
    url = f"{API_BASE}/users/username?username={username}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=PUBLIC_HEADERS) as resp:
            status = resp.status
            text = await resp.text()
            disponible = (status == 404)
            await interaction.response.send_message(
                f"**{username}**\n"
                f"Status: {status}\n"
                f"Respuesta: {text[:300]}\n\n"
                f"Interpretación: {'✅ DISPONIBLE' if disponible else '❌ OCUPADO o error'}"
            )

# ------------------------------------------------------------------
#  COMANDO /check
# ------------------------------------------------------------------

@bot.tree.command(name="check", description="Verifica nombres específicos (separados por comas o espacios)")
@app_commands.describe(
    lista="Lista de nombres (ej: 25649, 98331, 6290)",
    channel="Canal para el reporte (opcional)"
)
async def slash_check(interaction: discord.Interaction, lista: str, channel: discord.TextChannel = None):
    cleaned = lista.replace(',', ' ').replace('\n', ' ').replace('\r', ' ')
    names = [n.strip() for n in cleaned.split() if n.strip()]
    if not names:
        await interaction.response.send_message("No se proporcionaron nombres válidos.")
        return
    if len(names) > 100:
        names = names[:100]
        await interaction.response.send_message(f"Demasiados nombres, limitando a 100.")
    else:
        await interaction.response.send_message(f"Se recibieron {len(names)} nombres. Verificando...")

    available = await check_list(names)
    target = channel or interaction.channel
    await send_available_only(target, available, "check")
    if target != interaction.channel:
        await interaction.followup.send(f"Reporte enviado a {target.mention}")

# ------------------------------------------------------------------
#  COMANDOS GENERADORES
# ------------------------------------------------------------------

@bot.tree.command(name="numbers", description="Genera números y verifica disponibilidad")
@app_commands.describe(
    count="Cantidad (máx 50)",
    length="Longitud (2-10)",
    channel="Canal para el reporte (opcional)"
)
async def slash_numbers(interaction: discord.Interaction, count: int, length: int,
                        channel: discord.TextChannel = None):
    if count > 50: count = 50
    if length < 2: length = 2
    if length > 10: length = 10
    names = gen_numbers(count, length)
    await interaction.response.send_message(
        f"Generados {len(names)} números:\n" + "\n".join(names[:20]) +
        (f"\n... y {len(names)-20} más" if len(names) > 20 else "")
    )
    await interaction.followup.send("Verificando disponibilidad...")
    available = await check_list(names)
    target = channel or interaction.channel
    await send_available_only(target, available, "numbers")
    if target != interaction.channel:
        await interaction.followup.send(f"Reporte enviado a {target.mention}")

@bot.tree.command(name="alnum", description="Genera alfanuméricos y verifica")
@app_commands.describe(
    count="Cantidad (máx 50)",
    length="Longitud (3-12)",
    channel="Canal para el reporte"
)
async def slash_alnum(interaction: discord.Interaction, count: int, length: int,
                      channel: discord.TextChannel = None):
    if count > 50: count = 50
    if length < 3: length = 3
    if length > 12: length = 12
    names = gen_alnum(count, length)
    await interaction.response.send_message(
        f"Generados {len(names)} alfanuméricos:\n" + "\n".join(names[:20]) +
        (f"\n... y {len(names)-20} más" if len(names) > 20 else "")
    )
    await interaction.followup.send("Verificando...")
    available = await check_list(names)
    target = channel or interaction.channel
    await send_available_only(target, available, "alnum")
    if target != interaction.channel:
        await interaction.followup.send(f"Reporte enviado a {target.mention}")

@bot.tree.command(name="words", description="Genera palabras desde archivo y verifica")
@app_commands.describe(
    file="Archivo .txt con una palabra por línea",
    count="Cantidad (máx 50)",
    channel="Canal para el reporte"
)
async def slash_words(interaction: discord.Interaction, file: discord.Attachment,
                      count: int, channel: discord.TextChannel = None):
    if count > 50: count = 50
    if not file.filename.endswith(".txt"):
        await interaction.response.send_message("El archivo debe ser .txt")
        return
    try:
        content = await file.read()
        words = content.decode("utf-8").splitlines()
        words = [w.strip() for w in words if w.strip()]
    except Exception as e:
        await interaction.response.send_message(f"Error al leer archivo: {e}")
        return
    if not words:
        await interaction.response.send_message("La lista está vacía.")
        return
    names = gen_words(count, words)
    await interaction.response.send_message(
        f"Generadas {len(names)} palabras:\n" + "\n".join(names[:20]) +
        (f"\n... y {len(names)-20} más" if len(names) > 20 else "")
    )
    await interaction.followup.send("Verificando...")
    available = await check_list(names)
    target = channel or interaction.channel
    await send_available_only(target, available, "words")
    if target != interaction.channel:
        await interaction.followup.send(f"Reporte enviado a {target.mention}")

@bot.tree.command(name="pattern", description="Prefijo + dígitos y verifica")
@app_commands.describe(
    prefix="Prefijo alfanumérico (sin espacios)",
    count="Cantidad (máx 50)",
    num_len="Número de dígitos (1-6)",
    channel="Canal para el reporte"
)
async def slash_pattern(interaction: discord.Interaction, prefix: str, count: int,
                        num_len: int, channel: discord.TextChannel = None):
    if count > 50: count = 50
    if num_len < 1: num_len = 1
    if num_len > 6: num_len = 6
    if not prefix.isalnum():
        await interaction.response.send_message("El prefijo solo puede contener letras y números.")
        return
    names = gen_pattern(prefix, count, num_len)
    await interaction.response.send_message(
        f"Generados {len(names)} patrones:\n" + "\n".join(names[:20]) +
        (f"\n... y {len(names)-20} más" if len(names) > 20 else "")
    )
    await interaction.followup.send("Verificando...")
    available = await check_list(names)
    target = channel or interaction.channel
    await send_available_only(target, available, "pattern")
    if target != interaction.channel:
        await interaction.followup.send(f"Reporte enviado a {target.mention}")

# ------------------------------------------------------------------
#  SERVIDOR WEB PARA RAILWAY
# ------------------------------------------------------------------

from aiohttp import web

async def handle(request):
    return web.Response(text="Bot activo – verificador público (404 = disponible)")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=8080)
    await site.start()
    print("Servidor web iniciado en puerto 8080")

async def main():
    asyncio.create_task(start_web_server())
    await bot.start(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
