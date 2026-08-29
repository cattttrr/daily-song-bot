import os
import base64
import random

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands


# ============================================================
# RAILWAY VARIABLES
# ============================================================

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

SPOTIFY_MARKET = os.getenv("SPOTIFY_MARKET", "PL")


# ============================================================
# CHECK VARIABLES
# ============================================================

if not DISCORD_TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN")

if not SPOTIFY_CLIENT_ID:
    raise RuntimeError("Missing SPOTIFY_CLIENT_ID")

if not SPOTIFY_CLIENT_SECRET:
    raise RuntimeError("Missing SPOTIFY_CLIENT_SECRET")


# ============================================================
# DISCORD BOT
# ============================================================

intents = discord.Intents.default()

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# ============================================================
# SPOTIFY
# ============================================================

async def get_spotify_token():

    credentials = (
        f"{SPOTIFY_CLIENT_ID}:"
        f"{SPOTIFY_CLIENT_SECRET}"
    )

    encoded_credentials = base64.b64encode(
        credentials.encode("utf-8")
    ).decode("utf-8")

    headers = {
        "Authorization": f"Basic {encoded_credentials}",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    data = {
        "grant_type": "client_credentials"
    }

    async with aiohttp.ClientSession() as session:

        async with session.post(
            "https://accounts.spotify.com/api/token",
            headers=headers,
            data=data
        ) as response:

            if response.status != 200:

                text = await response.text()

                raise RuntimeError(
                    f"Spotify login failed: "
                    f"{response.status} {text}"
                )

            result = await response.json()

            return result["access_token"]


async def get_random_song():

    token = await get_spotify_token()

    # Random search words.
    # This gives Spotify many different possible results.
    search_words = [
        "a",
        "e",
        "i",
        "o",
        "u",
        "love",
        "night",
        "life",
        "time",
        "you",
        "me",
        "dream",
        "heart",
        "world",
        "star",
        "home",
        "fire",
        "rain",
        "blue",
        "music",
        "summer",
        "dance",
        "sky",
        "moon",
        "light"
    ]

    query = random.choice(search_words)

    headers = {
        "Authorization": f"Bearer {token}"
    }

    # Spotify allows up to 50 results per search.
    params = {
        "q": query,
        "type": "track",
        "market": SPOTIFY_MARKET,
        "limit": 50,
        "offset": random.randint(0, 950)
    }

    async with aiohttp.ClientSession() as session:

        async with session.get(
            "https://api.spotify.com/v1/search",
            headers=headers,
            params=params
        ) as response:

            if response.status != 200:

                text = await response.text()

                raise RuntimeError(
                    f"Spotify search failed: "
                    f"{response.status} {text}"
                )

            data = await response.json()

    tracks = data.get(
        "tracks",
        {}
    ).get(
        "items",
        []
    )

    # Remove invalid/local tracks.
    tracks = [
        track
        for track in tracks
        if track.get("id")
        and not track.get("is_local", False)
    ]

    if not tracks:

        raise RuntimeError(
            "Spotify didn't return any songs."
        )

    return random.choice(tracks)


# ============================================================
# CREATE DISCORD EMBED
# ============================================================

def create_song_embed(track):

    song_name = track.get(
        "name",
        "Unknown Song"
    )

    artists = track.get(
        "artists",
        []
    )

    artist_names = ", ".join(
        artist.get(
            "name",
            "Unknown Artist"
        )
        for artist in artists
    )

    album = track.get(
        "album",
        {}
    )

    album_name = album.get(
        "name",
        "Unknown Album"
    )

    spotify_url = (
        track
        .get("external_urls", {})
        .get("spotify")
    )

    embed = discord.Embed(
        title="🎵 Random Song",
        description=(
            f"## {song_name}\n"
            f"**{artist_names}**\n\n"
            f"💿 Album: **{album_name}**\n\n"
            f"🎧 [Listen on Spotify]({spotify_url})"
        )
    )

    images = album.get(
        "images",
        []
    )

    if images:

        embed.set_thumbnail(
            url=images[0]["url"]
        )

    embed.set_footer(
        text="Random song from Spotify 🎶"
    )

    return embed


# ============================================================
# /RANDOMSONG
# ============================================================

@bot.tree.command(
    name="randomsong",
    description="Post a random Spotify song in this channel."
)
async def randomsong(
    interaction: discord.Interaction
):

    # This command only works in a server channel.
    if interaction.guild is None:

        await interaction.response.send_message(
            "❌ This command can only be used in a server.",
            ephemeral=True
        )

        return

    # Let Discord know we're working.
    await interaction.response.defer()

    try:

        # Get random Spotify track.
        track = await get_random_song()

        # Make the Discord embed.
        embed = create_song_embed(track)

        # Send it to the channel where
        # /randomsong was used.
        await interaction.followup.send(
            "🎶 **Here's your random song!**",
            embed=embed
        )

        print(
            f"[INFO] Sent random song: "
            f"{track.get('name', 'Unknown')}"
        )

    except Exception as error:

        print(
            f"[ERROR] /randomsong failed: {error}"
        )

        await interaction.followup.send(
            (
                "❌ I couldn't get a song from Spotify.\n"
                "Check the Spotify Client ID and "
                "Client Secret in Railway."
            ),
            ephemeral=True
        )


# ============================================================
# BOT READY
# ============================================================

@bot.event
async def on_ready():

    print(
        "===================================="
    )

    print(
        f"Logged in as: {bot.user}"
    )

    print(
        f"Bot ID: {bot.user.id}"
    )

    print(
        "===================================="
    )

    try:

        synced = await bot.tree.sync()

        print(
            f"[INFO] Synced "
            f"{len(synced)} slash command(s)."
        )

    except Exception as error:

        print(
            f"[ERROR] Command sync failed: {error}"
        )


# ============================================================
# START
# ============================================================

bot.run(DISCORD_TOKEN)
