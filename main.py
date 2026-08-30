#!/usr/bin/env python3
"""
Discord bot – verificación de disponibilidad con doble estrategia.
Primero intenta GET /users/@me/username?username= (funciona con token de usuario).
Si da 404, fallback a PATCH con reversión.
Variables: DISCORD_BOT_TOKEN, DISCORD_USER_TOKEN, REQUEST_DELAY (opcional)
"""

import os
import asyncio
import aiohttp
import random
import string
import io
import json
from typing import List, Optional

import discord
from discord import app_commands
from discord.ext import commands

# ===== CONFIGURACIÓN =====
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_USER_TOKEN = os.getenv("DISCORD_USER_TOKEN")
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "2.0"))

if not DISCORD_BOT_TOKEN or not DISCORD_USER_TOKEN:
    raise RuntimeError("Faltan variables de entorno DISCORD_BOT_TOKEN o DISCORD_USER_TOKEN.")

USER_HEADERS = {
    "Authorization": DISCORD_USER_TOKEN,
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}
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
#  VERIFICADOR CON GET Y FALLBACK PATCH
# ------------------------------------------------------------------

async def get_current_username(session: aiohttp.ClientSession) -> Optional[str]:
    url = f"{API_BASE}/users/@me"
    try:
        async with session.get(url, headers=USER_HEADERS) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("username")
            else:
                print(f"Error al obtener username actual: {resp.status}")
                return None
    except Exception as e:
        print(f"Excepción: {e}")
        return None

async def check_via_get(session: aiohttp.ClientSession, username: str) -> Optional[bool]:
    """
    Intenta GET /users/@me/username?username=...
    Retorna True si disponible, False si no, None si error (para fallback)
    """
    url = f"{API_BASE}/users/@me/username?username={username}"
    try:
        async with session.get(url, headers=USER_HEADERS) as resp:
            status = resp.status
            text = await resp.text()
            print(f"  GET {username} → status {status}, respuesta: {text[:150]}")
            if status == 200:
                data = json.loads(text)
                return data.get("available", False)
            elif status == 429:
                retry = (await resp.json()).get("retry_after", 5)
                await asyncio.sleep(retry + 1)
                return await check_via_get(session, username)
            else:
                # 404, 401, 403, 500... no confiamos en GET, usamos fallback
                return None
    except Exception as e:
        print(f"  GET excepción: {e}")
        return None

async def check_via_patch(session: aiohttp.ClientSession, candidate: str, original: str) -> bool:
    """
    Intenta PATCH con reversión. Retorna True si disponible y revertido.
    """
    patch_url = f"{API_BASE}/users/@me"
    payload = {"username": candidate}
    try:
        async with session.patch(patch_url, headers=USER_HEADERS, json=payload) as resp:
            status = resp.status
            text = await resp.text()
            print(f"  PATCH {candidate} → status {status}, respuesta: {text[:150]}")
            if status == 200:
                # Revertir
                revert = {"username": original}
                async with session.patch(patch_url, headers=USER_HEADERS, json=revert) as rev:
                    if rev.status == 200:
                        print(f"  → revertido exitosamente")
                    else:
                        print(f"  → falló reversión (status {rev.status})")
                return True
            elif status == 400:
                if "taken" in text.lower() or "unavailable" in text.lower():
                    return False
                else:
                    # Otro error, p.ej. longitud
                    return False
            elif status == 429:
                retry = (await resp.json()).get("retry_after", 5)
                await asyncio.sleep(retry + 1)
                return await check_via_patch(session, candidate, original)
            else:
                # 401, 403, etc. -> fallo
                return False
    except Exception as e:
        print(f"  PATCH excepción: {e}")
        return False

async def check_username(session: aiohttp.ClientSession, username: str, original: str) -> bool:
    """
    Estrategia: primero GET, si devuelve None (error) usar PATCH.
    """
    result_get = await check_via_get(session, username)
    if result_get is not None:
        return result_get
    else:
        # Fallback a PATCH
        print(f"  GET falló, usando PATCH fallback para {username}")
        return await check_via_patch(session, username, original)

async def check_list(usernames: List[str], delay: float = REQUEST_DELAY) -> List[str]:
    async with aiohttp.ClientSession() as session:
        original = await get_current_username(session)
        if not original:
            print("ERROR: No se pudo obtener el nombre actual. Verifica el token.")
            return []
        print(f"Nombre actual: {original}")

        available = []
        total = len(usernames)
        for idx, name in enumerate(usernames, start=1):
            print(f"[{idx}/{total}] Verificando: {name}")
            if await check_username(session, name, original):
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
    # Verificar token de usuario
    async with aiohttp.ClientSession() as session:
        if await get_current_username(session):
            print("✅ TOKEN DE USUARIO VÁLIDO")
        else:
            print("❌ TOKEN DE USUARIO INVÁLIDO – las verificaciones fallarán.")

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
        description=f"Se encontraron {len(available)} nombres disponibles (ver archivo).",
        color=discord.Color.green()
    )
    preview = "\n".join(available[:10])
    if len(available) > 10:
        preview += f"\n... y {len(available)-10} más"
    embed.add_field(name="Vista previa", value=f"```\n{preview}```", inline=False)
    await channel.send(embed=embed, file=file)

# ------------------------------------------------------------------
#  COMANDOS SLASH
# ------------------------------------------------------------------

@bot.tree.command(name="numbers", description="Genera números y verifica disponibilidad")
@app_commands.describe(
    count="Cantidad (máx 50)",
    length="Longitud del número (1-10)",
    channel="Canal para el reporte (opcional)"
)
async def slash_numbers(interaction: discord.Interaction, count: int, length: int,
                        channel: discord.TextChannel = None):
    if count > 50: count = 50
    if length < 2: length = 2          # Discord mínimo 2 caracteres
    if length > 10: length = 10
    names = gen_numbers(count, length)
    await interaction.response.send_message(
        f"Generados {len(names)} números:\n" + "\n".join(names[:20]) +
        (f"\n... y {len(names)-20} más" if len(names) > 20 else "")
    )
    await interaction.followup.send("Verificando disponibilidad... (puede tardar)")
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
    return web.Response(text="Bot activo – verifica disponibilidad (GET + fallback PATCH)")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=8080)
    await site.start()
    print("Servidor web iniciado en puerto 8080")

# ------------------------------------------------------------------
#  EJECUCIÓN PRINCIPAL
# ------------------------------------------------------------------

async def main():
    asyncio.create_task(start_web_server())
    await bot.start(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
