"""Discord/Spotify Bot for Discord
    Helps collect playlists that were used to play music via bot commands and 
    consolidate individual songs, albums, and playlists into other playlists.
    I also have hopes for the future to do some data analysis on the music.
"""
from os import getenv
from dotenv import load_dotenv
from log_manager import setup_logging
from db_manager import DBManager
from spotify_manager import SpotifyManager, SpotifyManagerSettings
from discord_manager import DiscordManager, DiscordManagerSettings

# --- Load environment variables ---
load_dotenv()

# --- Setup your credentials from env ---
SPOTIFY_CLIENT_ID = getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = getenv("SPOTIFY_REDIRECT_URI")
SPOTIFY_USER_ID = getenv("SPOTIFY_USER_ID")

DISCORD_BOT_TOKEN = getenv("DISCORD_BOT_TOKEN")
TARGET_CHANNEL_NAME = getenv("TARGET_CHANNEL_NAME")
GUILD_IDS = [int(id) for id in getenv("GUILD_IDS").split(",")]

DB_URL = getenv("DB_URL")

LOGGING_FORMAT = getenv("LOGGING_FORMAT")
DATE_FORMAT = getenv("DATE_FORMAT")


# --- Initialize logging ---

setup_logging()

# --- Initialize Database ---

# opens/creates the db and ensures tables exist
db = DBManager(db_url=DB_URL)

# --- Initialize API managers ---

spotify_manager = SpotifyManager(
    settings=SpotifyManagerSettings(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
    )
)

discord_manager = DiscordManager(
    spotify_manager=spotify_manager,
    discord_settings=DiscordManagerSettings(
        target_channel=TARGET_CHANNEL_NAME,
        guild_ids=GUILD_IDS,
        user_id=SPOTIFY_USER_ID,
    )
)

# --- Run Discord bot ---
discord_manager.run(DISCORD_BOT_TOKEN, log_handler=None)
