import os
import base64
import random

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands


# ============================================================
# CONFIGURATION
# ============================================================

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

# Optional Spotify market.
# PL = Poland
# You can change this in Railway if you want.
SPOTIFY_MARKET = os.getenv("SPOTIFY_MARKET", "PL")


# ============================================================
# CHECK RAILWAY VARIABLES
# ============================================================

if not DISCORD_TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN is missing from Railway Variables."
    )

if not SPOTIFY_CLIENT_ID:
    raise RuntimeError(
        "SPOTIFY_CLIENT_ID is missing from Railway Variables."
    )

if not SPOTIFY_CLIENT_SECRET:
    raise RuntimeError(
        "SPOTIFY_CLIENT_SECRET is missing from Railway Variables."
    )


# ============================================================
# DISCORD BOT
# ============================================================

intents = discord.Intents.default()

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# ============================================================
# GET SPOTIFY ACCESS TOKEN
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
        "Authorization": (
            f"Basic {encoded_credentials}"
        ),
        "Content-Type": (
            "application/x-www-form-urlencoded"
        )
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

            response_text = await response.text()

            if response.status != 200:

                print(
                    "[ERROR] Spotify authentication failed:"
                )

                print(
                    f"Status: {response.status}"
                )

                print(
                    f"Response: {response_text}"
                )

                raise RuntimeError(
                    "Spotify authentication failed."
                )

            result = await response.json()

            token = result.get(
                "access_token"
            )

            if not token:

                raise RuntimeError(
                    "Spotify did not provide an access token."
                )

            return token


# ============================================================
# GET RANDOM SONG FROM SPOTIFY
# ============================================================

async def get_random_song():

    token = await get_spotify_token()

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
        "light",
        "happy",
        "sad",
        "rock",
        "pop",
        "party"
    ]

    query = random.choice(
        search_words
    )

    headers = {
        "Authorization": (
            f"Bearer {token}"
        )
    }

    # Spotify search supports a maximum
    # of 10 results per request.
    params = {
        "q": query,
        "type": "track",
        "market": SPOTIFY_MARKET,
        "limit": 10,
        "offset": random.randint(0, 100)
    }

    async with aiohttp.ClientSession() as session:

        async with session.get(
            "https://api.spotify.com/v1/search",
            headers=headers,
            params=params
        ) as response:

            response_text = await response.text()

            if response.status != 200:

                print(
                    "[ERROR] Spotify search failed:"
                )

                print(
                    f"Status: {response.status}"
                )

                print(
                    f"Response: {response_text}"
                )

                raise RuntimeError(
                    "Spotify search failed."
                )

            data = await response.json()

    tracks = (
        data
        .get("tracks", {})
        .get("items", [])
    )

    tracks = [
        track
        for track in tracks
        if track.get("id")
        and not track.get(
            "is_local",
            False
        )
    ]

    if not tracks:

        raise RuntimeError(
            "Spotify returned no songs."
        )

    return random.choice(
        tracks
    )


# ============================================================
# CREATE SONG EMBED
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
        .get(
            "external_urls",
            {}
        )
        .get(
            "spotify"
        )
    )

    embed = discord.Embed(
        title="🎵 Random Song",
        description=(
            f"## {song_name}\n"
            f"**{artist_names}**\n\n"
            f"💿 Album: **{album_name}**\n\n"
            f"🎧 [Listen on Spotify]"
            f"({spotify_url})"
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
    description=(
        "Post a random Spotify song "
        "in this channel."
    )
)
async def randomsong(
    interaction: discord.Interaction
):
    # Must be the first thing – protects against the 3-second timeout
    await interaction.response.defer()

    if interaction.guild is None:
        await interaction.followup.send(
            "❌ This command can only be used in a server.",
            ephemeral=True
        )
        return

    try:

        print(
            f"[INFO] /randomsong used by "
            f"{interaction.user} "
            f"in #{interaction.channel}"
        )

        # Get a random Spotify song.
        track = await get_random_song()

        # Create the Discord embed.
        embed = create_song_embed(
            track
        )

        # Send the song in the SAME channel
        # where /randomsong was used.
        await interaction.followup.send(
            content=(
                "🎶 **Here's your random song!**"
            ),
            embed=embed
        )

        print(
            f"[INFO] Successfully sent: "
            f"{track.get('name', 'Unknown Song')}"
        )

    except Exception as error:

        print(
            "===================================="
        )

        print(
            "[ERROR] /randomsong failed:"
        )

        print(
            repr(error)
        )

        print(
            "===================================="
        )

        await interaction.followup.send(
            (
                "❌ I couldn't get a song from Spotify.\n\n"
                "Check the Railway logs for the "
                "exact Spotify error."
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
        "       RANDOM SONG BOT ONLINE"
    )

    print(
        "===================================="
    )

    print(
        f"Logged in as: {bot.user}"
    )

    print(
        f"Bot ID: {bot.user.id}"
    )

    try:

        synced = await bot.tree.sync()

        print(
            f"[INFO] Synced "
            f"{len(synced)} slash command(s)."
        )

    except Exception as error:

        print(
            "[ERROR] Failed to sync slash commands:"
        )

        print(
            repr(error)
        )

    print(
        "===================================="
    )


# ============================================================
# START BOT
# ============================================================

bot.run(
    DISCORD_TOKEN
)
