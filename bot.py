import os
import base64
import random
import sqlite3
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks


# ============================================================
# CONFIG
# ============================================================

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

# Default timezone: Poland
DAILY_TIMEZONE = os.getenv("DAILY_TIMEZONE", "Europe/Warsaw")

# Default posting time: 12:00
DAILY_TIME = os.getenv("DAILY_TIME", "12:00")

# Spotify market
SPOTIFY_MARKET = os.getenv("SPOTIFY_MARKET", "PL")

# Railway Volume should be mounted at /app/data
DATA_DIR = os.getenv("DATA_DIR", "/app/data")

DATABASE_PATH = os.path.join(DATA_DIR, "bot.db")


# ============================================================
# VALIDATION
# ============================================================

if not DISCORD_TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN")

if not SPOTIFY_CLIENT_ID:
    raise RuntimeError("Missing SPOTIFY_CLIENT_ID")

if not SPOTIFY_CLIENT_SECRET:
    raise RuntimeError("Missing SPOTIFY_CLIENT_SECRET")


# ============================================================
# DATABASE
# ============================================================

os.makedirs(DATA_DIR, exist_ok=True)


def get_db():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database():
    with get_db() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                last_sent_date TEXT,
                last_track_id TEXT
            )
            """
        )

        db.commit()


def set_guild_channel(guild_id: int, channel_id: int):
    with get_db() as db:
        db.execute(
            """
            INSERT INTO guild_settings
                (guild_id, channel_id, last_sent_date, last_track_id)
            VALUES
                (?, ?, NULL, NULL)
            ON CONFLICT(guild_id)
            DO UPDATE SET channel_id = excluded.channel_id
            """,
            (guild_id, channel_id)
        )

        db.commit()


def get_guild_settings(guild_id: int):
    with get_db() as db:
        row = db.execute(
            """
            SELECT *
            FROM guild_settings
            WHERE guild_id = ?
            """,
            (guild_id,)
        ).fetchone()

    return row


def get_all_guild_settings():
    with get_db() as db:
        rows = db.execute(
            """
            SELECT *
            FROM guild_settings
            """
        ).fetchall()

    return rows


def mark_song_sent(
    guild_id: int,
    sent_date: str,
    track_id: str
):
    with get_db() as db:
        db.execute(
            """
            UPDATE guild_settings
            SET last_sent_date = ?,
                last_track_id = ?
            WHERE guild_id = ?
            """,
            (sent_date, track_id, guild_id)
        )

        db.commit()


def remove_guild(guild_id: int):
    with get_db() as db:
        db.execute(
            """
            DELETE FROM guild_settings
            WHERE guild_id = ?
            """,
            (guild_id,)
        )

        db.commit()


initialize_database()


# ============================================================
# TIME HELPERS
# ============================================================

try:
    BOT_TIMEZONE = ZoneInfo(DAILY_TIMEZONE)
except Exception:
    print(
        f"[WARNING] Invalid DAILY_TIMEZONE "
        f"'{DAILY_TIMEZONE}'. Using UTC."
    )

    BOT_TIMEZONE = ZoneInfo("UTC")


def get_current_local_time():
    return datetime.now(BOT_TIMEZONE)


def is_daily_time():
    """
    Returns True when the configured daily time has been reached.
    """

    try:
        hour, minute = map(int, DAILY_TIME.split(":"))

        now = get_current_local_time()

        target_minutes = hour * 60 + minute
        current_minutes = now.hour * 60 + now.minute

        return current_minutes >= target_minutes

    except Exception:
        print(
            "[WARNING] DAILY_TIME must be in HH:MM format. "
            "Example: 12:00"
        )

        return False


def get_today_string():
    return get_current_local_time().strftime("%Y-%m-%d")


# ============================================================
# SPOTIFY
# ============================================================

class SpotifyClient:

    def __init__(self):
        self.access_token = None
        self.expires_at = 0

    async def get_access_token(self):

        now = datetime.now().timestamp()

        if (
            self.access_token
            and now < self.expires_at - 60
        ):
            return self.access_token

        credentials = (
            f"{SPOTIFY_CLIENT_ID}:"
            f"{SPOTIFY_CLIENT_SECRET}"
        )

        encoded = base64.b64encode(
            credentials.encode()
        ).decode()

        headers = {
            "Authorization": f"Basic {encoded}",
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

                if response.status != 200:
                    text = await response.text()

                    raise RuntimeError(
                        "Spotify authentication failed: "
                        f"{response.status} {text}"
                    )

                result = await response.json()

        self.access_token = result["access_token"]

        self.expires_at = (
            now + result["expires_in"]
        )

        return self.access_token

    async def search_tracks(
        self,
        query: str,
        offset: int = 0
    ):

        token = await self.get_access_token()

        headers = {
            "Authorization": f"Bearer {token}"
        }

        params = {
            "q": query,
            "type": "track",
            "market": SPOTIFY_MARKET,
            "limit": 10,
            "offset": offset
        }

        async with aiohttp.ClientSession() as session:

            async with session.get(
                "https://api.spotify.com/v1/search",
                headers=headers,
                params=params
            ) as response:

                if response.status == 401:

                    self.access_token = None
                    self.expires_at = 0

                    token = await self.get_access_token()

                    headers["Authorization"] = (
                        f"Bearer {token}"
                    )

                    async with session.get(
                        "https://api.spotify.com/v1/search",
                        headers=headers,
                        params=params
                    ) as retry:

                        if retry.status != 200:
                            text = await retry.text()

                            raise RuntimeError(
                                f"Spotify search failed: "
                                f"{retry.status} {text}"
                            )

                        return await retry.json()

                if response.status != 200:
                    text = await response.text()

                    raise RuntimeError(
                        f"Spotify search failed: "
                        f"{response.status} {text}"
                    )

                return await response.json()

    async def get_random_song(
        self,
        previous_track_id=None
    ):

        # Different queries make the selection less predictable.
        queries = [
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
            "music"
        ]

        query = random.choice(queries)

        # First request to discover the result count.
        first_result = await self.search_tracks(
            query=query,
            offset=0
        )

        track_data = first_result.get(
            "tracks",
            {}
        )

        total = track_data.get("total", 0)

        if total <= 0:
            raise RuntimeError(
                "Spotify returned no tracks."
            )

        # Spotify currently caps search requests at 10 items.
        # Pick a random valid offset.
        maximum_offset = max(
            0,
            min(total - 1, 1000)
        )

        offset = random.randint(
            0,
            maximum_offset
        )

        result = await self.search_tracks(
            query=query,
            offset=offset
        )

        tracks = result.get(
            "tracks",
            {}
        ).get(
            "items",
            []
        )

        tracks = [
            track
            for track in tracks
            if not track.get("is_local", False)
        ]

        if previous_track_id:
            different_tracks = [
                track
                for track in tracks
                if track.get("id") != previous_track_id
            ]

            if different_tracks:
                tracks = different_tracks

        if not tracks:
            raise RuntimeError(
                "Spotify returned no usable tracks."
            )

        return random.choice(tracks)


spotify = SpotifyClient()


# ============================================================
# EMBED
# ============================================================

def create_song_embed(track):

    track_name = track.get(
        "name",
        "Unknown song"
    )

    artists = track.get(
        "artists",
        []
    )

    artist_names = ", ".join(
        artist.get(
            "name",
            "Unknown artist"
        )
        for artist in artists
    )

    album = track.get(
        "album",
        {}
    )

    album_name = album.get(
        "name",
        "Unknown album"
    )

    spotify_url = (
        track
        .get("external_urls", {})
        .get("spotify")
    )

    embed = discord.Embed(
        title="🎵 Song of the Day",
        description=(
            f"## {track_name}\n"
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
        text="Random song selected from Spotify"
    )

    return embed


# ============================================================
# SEND DAILY SONG
# ============================================================

async def send_song_to_guild(
    guild_id: int,
    force: bool = False
):

    settings = get_guild_settings(guild_id)

    if not settings:
        return False

    channel_id = settings["channel_id"]

    channel = bot.get_channel(channel_id)

    if channel is None:

        try:
            channel = await bot.fetch_channel(
                channel_id
            )

        except discord.DiscordException as error:
            print(
                f"[ERROR] Could not find channel "
                f"{channel_id}: {error}"
            )

            return False

    today = get_today_string()

    # Prevent duplicate daily songs.
    if (
        not force
        and settings["last_sent_date"] == today
    ):
        return False

    try:

        track = await spotify.get_random_song(
            previous_track_id=settings[
                "last_track_id"
            ]
        )

        embed = create_song_embed(track)

        await channel.send(
            content=(
                "🎶 **Today's random song!**"
            ),
            embed=embed
        )

        mark_song_sent(
            guild_id=guild_id,
            sent_date=today,
            track_id=track["id"]
        )

        print(
            f"[INFO] Sent song '{track['name']}' "
            f"to guild {guild_id}"
        )

        return True

    except discord.Forbidden:
        print(
            f"[ERROR] No permission to send messages "
            f"in channel {channel_id}"
        )

        return False

    except discord.HTTPException as error:
        print(
            f"[ERROR] Discord error for guild "
            f"{guild_id}: {error}"
        )

        return False

    except Exception as error:
        print(
            f"[ERROR] Song error for guild "
            f"{guild_id}: {error}"
        )

        return False


# ============================================================
# DAILY SCHEDULER
# ============================================================

@tasks.loop(minutes=1)
async def daily_scheduler():

    if not is_daily_time():
        return

    print(
        "[INFO] Checking daily song schedule..."
    )

    settings_list = get_all_guild_settings()

    for settings in settings_list:

        guild_id = settings["guild_id"]

        try:

            await send_song_to_guild(
                guild_id=guild_id
            )

        except Exception as error:

            print(
                f"[ERROR] Scheduler error for "
                f"guild {guild_id}: {error}"
            )

        # Avoid hammering APIs if many servers use the bot.
        await asyncio.sleep(1)


@daily_scheduler.before_loop
async def before_scheduler():

    await bot.wait_until_ready()


# ============================================================
# DISCORD BOT
# ============================================================

intents = discord.Intents.default()

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# ============================================================
# SETUP VIEW
# ============================================================

class SetupView(discord.ui.View):

    def __init__(self, author_id: int):
        super().__init__(timeout=180)

        self.author_id = author_id
        self.selected_channel = None

    async def interaction_check(
        self,
        interaction: discord.Interaction
    ):

        if interaction.user.id != self.author_id:

            await interaction.response.send_message(
                "❌ Only the person who started setup "
                "can use this menu.",
                ephemeral=True
            )

            return False

        return True

    @discord.ui.ChannelSelect(
        placeholder="Choose the daily song channel...",
        channel_types=[
            discord.ChannelType.text
        ],
        min_values=1,
        max_values=1
    )
    async def channel_select(
        self,
        interaction: discord.Interaction,
        select: discord.ui.ChannelSelect
    ):

        self.selected_channel = select.values[0]

        await interaction.response.send_message(
            f"✅ Selected "
            f"{self.selected_channel.mention}!",
            ephemeral=True
        )

    @discord.ui.Button(
        label="Save Setup",
        style=discord.ButtonStyle.success,
        emoji="💾"
    )
    async def save_setup(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        if self.selected_channel is None:

            await interaction.response.send_message(
                "❌ Choose a channel first!",
                ephemeral=True
            )

            return

        guild = interaction.guild

        if guild is None:

            await interaction.response.send_message(
                "❌ This can only be used in a server.",
                ephemeral=True
            )

            return

        channel = self.selected_channel

        me = guild.me

        if me is None:

            await interaction.response.send_message(
                "❌ I couldn't check my permissions.",
                ephemeral=True
            )

            return

        permissions = channel.permissions_for(me)

        if not permissions.view_channel:

            await interaction.response.send_message(
                "❌ I can't view that channel.",
                ephemeral=True
            )

            return

        if not permissions.send_messages:

            await interaction.response.send_message(
                "❌ I can't send messages in that channel.",
                ephemeral=True
            )

            return

        if not permissions.embed_links:

            await interaction.response.send_message(
                "❌ I need the **Embed Links** permission "
                "in that channel.",
                ephemeral=True
            )

            return

        set_guild_channel(
            guild_id=guild.id,
            channel_id=channel.id
        )

        await interaction.response.edit_message(
            content=(
                "✅ **Setup complete!**\n\n"
                f"Daily songs will be posted in "
                f"{channel.mention}.\n\n"
                f"⏰ Posting time: **{DAILY_TIME}**\n"
                f"🌍 Timezone: **{DAILY_TIMEZONE}**"
            ),
            embed=None,
            view=None
        )

        self.stop()

    @discord.ui.Button(
        label="Cancel",
        style=discord.ButtonStyle.danger,
        emoji="❌"
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        await interaction.response.edit_message(
            content="❌ Setup cancelled.",
            embed=None,
            view=None
        )

        self.stop()


# ============================================================
# /setup
# ============================================================

@bot.tree.command(
    name="setup",
    description=(
        "Choose the channel for the daily "
        "random Spotify song."
    )
)
@app_commands.default_permissions(
    manage_guild=True
)
@app_commands.checks.has_permissions(
    manage_guild=True
)
async def setup(
    interaction: discord.Interaction
):

    if interaction.guild is None:

        await interaction.response.send_message(
            "❌ This command only works inside a server.",
            ephemeral=True
        )

        return

    embed = discord.Embed(
        title="🎵 Daily Song Setup",
        description=(
            "Choose the channel where I should "
            "post a random Spotify song every day.\n\n"
            "1️⃣ Select a channel\n"
            "2️⃣ Press **Save Setup**\n"
            "3️⃣ Done! 🎶"
        )
    )

    current = get_guild_settings(
        interaction.guild.id
    )

    if current:

        channel = interaction.guild.get_channel(
            current["channel_id"]
        )

        if channel:

            embed.add_field(
                name="Current channel",
                value=channel.mention,
                inline=False
            )

    view = SetupView(
        interaction.user.id
    )

    await interaction.response.send_message(
        embed=embed,
        view=view,
        ephemeral=True
    )


# ============================================================
# /test
# ============================================================

@bot.tree.command(
    name="test",
    description=(
        "Immediately send a random Spotify song."
    )
)
@app_commands.default_permissions(
    manage_guild=True
)
@app_commands.checks.has_permissions(
    manage_guild=True
)
async def test(
    interaction: discord.Interaction
):

    if interaction.guild is None:

        await interaction.response.send_message(
            "❌ This command only works inside a server.",
            ephemeral=True
        )

        return

    settings = get_guild_settings(
        interaction.guild.id
    )

    if not settings:

        await interaction.response.send_message(
            "❌ This server isn't configured yet.\n"
            "Use `/setup` first.",
            ephemeral=True
        )

        return

    await interaction.response.defer(
        ephemeral=True
    )

    success = await send_song_to_guild(
        guild_id=interaction.guild.id,
        force=True
    )

    if success:

        channel = interaction.guild.get_channel(
            settings["channel_id"]
        )

        if channel:

            message = (
                f"✅ Song sent to "
                f"{channel.mention}!"
            )

        else:

            message = "✅ Song sent!"

    else:

        message = (
            "❌ I couldn't send the song. "
            "Check my permissions and Spotify "
            "configuration."
        )

    await interaction.followup.send(
        message,
        ephemeral=True
    )


# ============================================================
# /status
# ============================================================

@bot.tree.command(
    name="status",
    description=(
        "Show the current daily song configuration."
    )
)
async def status(
    interaction: discord.Interaction
):

    if interaction.guild is None:

        await interaction.response.send_message(
            "❌ This command only works inside a server.",
            ephemeral=True
        )

        return

    settings = get_guild_settings(
        interaction.guild.id
    )

    if not settings:

        await interaction.response.send_message(
            "❌ Daily songs are not configured.\n"
            "Use `/setup`.",
            ephemeral=True
        )

        return

    channel = interaction.guild.get_channel(
        settings["channel_id"]
    )

    if channel is None:

        await interaction.response.send_message(
            "⚠️ The configured channel no longer exists.\n"
            "Run `/setup` again.",
            ephemeral=True
        )

        return

    last_song = settings["last_sent_date"]

    await interaction.response.send_message(
        (
            "🎵 **Daily Song Status**\n\n"
            f"📢 Channel: {channel.mention}\n"
            f"⏰ Time: **{DAILY_TIME}**\n"
            f"🌍 Timezone: **{DAILY_TIMEZONE}**\n"
            f"📅 Last song: "
            f"**{last_song or 'None yet'}**"
        ),
        ephemeral=True
    )


# ============================================================
# /disable
# ============================================================

@bot.tree.command(
    name="disable",
    description=(
        "Disable daily songs for this server."
    )
)
@app_commands.default_permissions(
    manage_guild=True
)
@app_commands.checks.has_permissions(
    manage_guild=True
)
async def disable(
    interaction: discord.Interaction
):

    if interaction.guild is None:

        await interaction.response.send_message(
            "❌ This command only works inside a server.",
            ephemeral=True
        )

        return

    settings = get_guild_settings(
        interaction.guild.id
    )

    if not settings:

        await interaction.response.send_message(
            "ℹ️ Daily songs are already disabled.",
            ephemeral=True
        )

        return

    remove_guild(
        interaction.guild.id
    )

    await interaction.response.send_message(
        "✅ Daily songs have been disabled.",
        ephemeral=True
    )


# ============================================================
# BOT EVENTS
# ============================================================

@bot.event
async def on_ready():

    print(
        f"===================================="
    )

    print(
        f"Logged in as: {bot.user}"
    )

    print(
        f"Bot ID: {bot.user.id}"
    )

    print(
        f"Daily time: {DAILY_TIME}"
    )

    print(
        f"Timezone: {DAILY_TIMEZONE}"
    )

    print(
        f"Database: {DATABASE_PATH}"
    )

    print(
        f"===================================="
    )

    try:

        synced = await bot.tree.sync()

        print(
            f"[INFO] Synced "
            f"{len(synced)} slash commands."
        )

    except Exception as error:

        print(
            f"[ERROR] Slash command sync failed: "
            f"{error}"
        )

    if not daily_scheduler.is_running():

        daily_scheduler.start()


# ============================================================
# COMMAND ERRORS
# ============================================================

@setup.error
async def setup_error(
    interaction: discord.Interaction,
    error
):

    if isinstance(
        error,
        app_commands.errors.MissingPermissions
    ):

        await interaction.response.send_message(
            "❌ You need the **Manage Server** "
            "permission.",
            ephemeral=True
        )

        return

    print(
        f"[ERROR] /setup: {error}"
    )


@test.error
async def test_error(
    interaction: discord.Interaction,
    error
):

    if isinstance(
        error,
        app_commands.errors.MissingPermissions
    ):

        await interaction.response.send_message(
            "❌ You need the **Manage Server** "
            "permission.",
            ephemeral=True
        )

        return

    print(
        f"[ERROR] /test: {error}"
    )


@disable.error
async def disable_error(
    interaction: discord.Interaction,
    error
):

    if isinstance(
        error,
        app_commands.errors.MissingPermissions
    ):

        await interaction.response.send_message(
            "❌ You need the **Manage Server** "
            "permission.",
            ephemeral=True
        )

        return

    print(
        f"[ERROR] /disable: {error}"
    )


# ============================================================
# START
# ============================================================

bot.run(DISCORD_TOKEN)
