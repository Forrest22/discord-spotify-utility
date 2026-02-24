"""Discord/Spotify Bot for Discord
    Helps collect playlists that were used to play music via bot commands and 
    consolidate individual songs, albums, and playlists into other playlists.
    I also have hopes for the future to do some data analysis on the music.
"""
import os
import logging
from dotenv import load_dotenv
from spotify_manager import SpotifyManager
from discord_manager import DiscordManager

# --- Load environment variables ---
load_dotenv()

# --- Setup your credentials from env ---
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI")
SPOTIFY_USER_ID = os.getenv("SPOTIFY_USER_ID")

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
TARGET_CHANNEL_NAME = os.getenv("TARGET_CHANNEL_NAME")
GUILD_ID = os.getenv("GUILD_ID")

LOGGING_FORMAT = os.getenv("LOGGING_FORMAT")
DATE_FORMAT = os.getenv("DATE_FORMAT")

# --- Initialize logging ---
logging.basicConfig(level=logging.INFO, format=LOGGING_FORMAT, datefmt=DATE_FORMAT)
logger = logging.getLogger("")
log_handler = logging.FileHandler(
    filename="discord-spotify-util.log", encoding="utf-8", mode="w"
)

# --- Initialize managers ---
spotify_manager = SpotifyManager(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET,
    redirect_uri=SPOTIFY_REDIRECT_URI,
    # logger=logger,
)

discord_manager = DiscordManager(
    spotify_manager=spotify_manager,
    target_channel=TARGET_CHANNEL_NAME,
    guild_id=GUILD_ID,
    user_id=SPOTIFY_USER_ID,
    logger=logger,
)

# --- Run Discord bot ---
discord_manager.run(DISCORD_BOT_TOKEN)
