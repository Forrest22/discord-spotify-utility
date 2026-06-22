# Setup

Here's everything you need to get the bot running.

## Prerequisites

Before you start, you'll need:

- **Python 3.12**
- **A Spotify app** — create one at the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
  You'll need the client ID, client secret, and a redirect URI (e.g. `https://127.0.0.1:8888/callback`).
- **A Discord bot** — create one at the [Discord Developer Portal](https://discord.com/developers/applications).
  Enable the **Message Content Intent** and **Server Members Intent** under Bot settings, then invite the
  bot to your server with the `bot` and `applications.commands` scopes.

## Install

Clone the repo and set up a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate        # macOS / Linux
venv\Scripts\activate           # Windows
pip install -r requirements.txt
```

## Voice playback (optional but recommended)

The music player needs a couple of system-level packages that aren't on PyPI:

```bash
sudo apt install ffmpeg libopus0
```

- `ffmpeg` handles audio processing. `libopus0` provides the Opus codec Discord uses.
- `libopus0` is usually pre-installed, but can be missing on minimal setups.
- `davey` (already in `requirements.txt`) is also required — it implements Discord's
  mandatory end-to-end voice encryption. The bot's voice connection will be rejected
  immediately without it.

## Configure credentials

Copy the example env file and fill it in:

```bash
cp .env.example .env
```

Then open `.env` and set these values:

| Variable | What it is |
|---|---|
| `SPOTIFY_CLIENT_ID` | Your Spotify app's client ID |
| `SPOTIFY_CLIENT_SECRET` | Your Spotify app's client secret |
| `SPOTIFY_REDIRECT_URI` | Redirect URI registered in your Spotify app (e.g. `https://127.0.0.1:8888/callback`) |
| `SPOTIFY_USER_ID` | Your Spotify username (used when building playlists) |
| `DISCORD_BOT_TOKEN` | Your bot's token from the Discord Developer Portal |
| `TARGET_CHANNEL_NAME` | Name of the text channel the bot should scan by default |
| `GUILD_IDS` | Comma-separated list of Discord server IDs the bot should register commands to |
| `DB_URL` | SQLite path, e.g. `sqlite:////absolute/path/to/storage/discord-spotify.db` (optional — defaults to `storage/discord-spotify.db` in the repo) |
| `MUSIC_DJ_ROLE_NAME` | Discord role required to use music/voice commands (e.g. `DJ`) |
| `LOGGING_FORMAT` | Log line format (optional — defaults to a timestamp + level + name format) |
| `DATE_FORMAT` | Timestamp format for logs (optional) |

## Run

```bash
python src/main.py
```

On first run the bot will create the database automatically in `storage/`. It will also
prompt you to log in to Spotify in your browser to authorize the app — this only happens
once and the token is cached locally.

## Using the bot

Once it's running, invite it to a voice channel or text channel and use `/help` to see
all available commands. The typical flow is:

1. `/scan` — pick up all the Spotify links shared in the channel
2. `/sync_metadata` — enrich them with track, artist, and genre info
3. `/stats` or `/genre_cloud` — see what you've been listening to

### Music trivia

Join a voice channel and run `/trivia_start` to kick off a game. The bot joins your channel
and plays songs — type your guesses right in the text channel. Faster, more complete guesses
score more points (500 max in the first 5 seconds, decaying to a floor of 100 over 60 seconds).

- `/trivia_start genre:rock rounds:5` — start a game (genre and round count are optional)
- `/trivia_skip` — skip the current song
- `/trivia_stop` — end the game early
- `/trivia_scores` — show the leaderboard (pass `days:0` for all-time)

## Linting

```bash
python -m pylint src
```

There's no test suite.

## Working on it locally

A few shortcuts that are handy when making changes:

- `source venv/bin/activate` — activate the virtual environment
- `venv/bin/pip install <LIBRARY>` — add a dependency while inside the venv
