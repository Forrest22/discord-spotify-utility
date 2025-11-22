# main.py
from dotenv import load_dotenv
import os

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

# --- Initialize managers ---
spotify_manager = SpotifyManager(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET,
    redirect_uri=SPOTIFY_REDIRECT_URI,
)

discord_manager = DiscordManager(
    spotify_manager=spotify_manager,
    target_channel=TARGET_CHANNEL_NAME,
    user_id=SPOTIFY_USER_ID,
)

# --- Run Discord bot ---
discord_manager.run(DISCORD_BOT_TOKEN)
