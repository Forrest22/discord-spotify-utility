# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Setup**
```bash
source venv/bin/activate          # activate venv (Linux/Mac)
venv\Scripts\activate             # activate venv (Windows)
pip install -r requirements.txt   # install dependencies
cp .env.example .env              # then fill in your credentials
```

**System dependencies (voice playback)**
```bash
sudo apt install ffmpeg libopus0  # FFmpeg (audio processing) + Opus codec
pip install davey                  # DAVE/E2EE protocol (already in requirements.txt)
```
`ffmpeg` and `libopus0` are not pip packages. `libopus0` is usually pre-installed
but may be missing on minimal hosts. `yt-dlp` (in requirements.txt) needs
occasional version bumps when YouTube changes its extraction — update the pin in
`requirements.txt` and reinstall when audio stops working.

`davey` is a compiled Rust extension in `requirements.txt` that provides Discord's
mandatory DAVE (Audio & Video End-to-End Encryption) protocol support. Without it,
voice connections are immediately rejected with close code 4017. A prebuilt
manylinux wheel is published for CPython 3.12 x86_64; other platforms may need a Rust
toolchain to build from source.

**Run**
```bash
python src/main.py
```

**Lint**
```bash
python -m pylint src              # from repo root, with venv active
```

There is no test suite.

## Architecture

The bot is a Discord client that scans channel history for Spotify URLs, persists them to a database, enriches them with track/artist/genre metadata, and provides music statistics and genre visualizations. Creating a Spotify playlist is optional. Entry point is `src/main.py`.

**Dependency flow:**
```
main.py
  └─ DBManager          (SQLAlchemy, sqlite by default)
  └─ SpotifyManager     (spotipy wrapper, holds DBManager ref)
  └─ StatsManager       (analytics query layer, holds DBManager ref)
  └─ DiscordManager     (discord.py subclass, holds all three above)
```

**Bot workflow:**
1. `/scan [limit] [period]` — scans channel history, records non-bot messages that contain Spotify links to DB. `period` filters by message date (`day|week|month|year|all`, default `all`).
   - `/play` and `/playnext` also feed the DB directly: when the user confirms a play with a Spotify URL, `DiscordManager.__record_played_link` records the link immediately (keyed on the interaction snowflake, attributed to the requesting user). Free-text / YouTube queries are not recorded. DB errors are logged but never interrupt playback.
2. `/sync_metadata` — enriches stored links: resolves albums/playlists to individual tracks, batch-fetches track/artist/album metadata from Spotify API, persists genres
3. `/stats [type] [period]` and `/genre_cloud [period]` — query the enriched data (no data until step 2 runs); includes songs played through the bot as well as links shared in chat
4. `/build_playlist [period]` — **optional** — builds a Spotify playlist from previously scanned links for this channel, optionally filtered by period

**`DiscordManager` (`src/discord_manager.py`)** subclasses `discord.Client` directly. Slash commands are defined inside `setup_hook()` as nested functions and synced to each guild in `GUILD_IDS`. All blocking Spotify and DB calls run in a thread executor via `asyncio.get_running_loop().run_in_executor()` to avoid blocking the asyncio event loop. An `asyncio.Lock` class variable (`_scan_lock`) prevents overlapping scans. `/help` auto-generates from `self.tree.get_commands()`, grouping commands by `HELP_CATEGORIES` (any command not listed there falls into an "Other" group, so new commands always appear). `/scan` skips the bot's own messages and messages with no Spotify links, so only real shared links land in the DB.

**`SpotifyManager` (`src/spotify_manager.py`)** wraps spotipy. `add_tracks_to_playlist` resolves mixed track/album/playlist URLs to individual URIs and batches `playlist_add_items` in chunks of 100 (Spotify API limit). `sync_metadata()` is the enrichment pipeline: resolves all stored `SpotifyLink` rows to track IDs, then batch-fetches track (`sp.tracks`, 50/call), artist (`sp.artists`, 50/call), and album metadata (`sp.albums`, 20/call), upserts them to the DB in dependency order (artists → albums → tracks → track_shares).

**`DBManager` (`src/db_manager.py`)** uses SQLAlchemy 2.x with mapped dataclasses. Two layers of models:
- *Discord layer*: `Guild → Channel → Message → SpotifyLink` (with `DiscordUser` on `Message`)
- *Enrichment layer*: `Artist`, `Album`, `Track` (linked via `track_artists` M2M association table), `TrackShare` (analytics fact table: one row per message × resolved track)

Every model has a `raw_data: JSON` column. The public `session()` context manager auto-commits or rolls back. All write methods return `None` (not ORM objects) to avoid detached-instance issues across session boundaries. Tables are created automatically on startup. Default DB path is `storage/discord-spotify.db`.

**`StatsManager` (`src/stats_manager.py`)** is a thin query layer. `top_tracks/albums/artists(period, n)` return `list[tuple[name, count]]`. `genre_frequencies(period)` joins `TrackShare → track_artists → Artist.genres`, flattens the JSON genre lists, and returns a `dict[genre, count]`. Period is one of `day|week|month|year|all`.

**`viz.py`** — `render_genre_cloud(freqs, out_path)` renders a word cloud PNG using the `wordcloud` library. Called in an executor since it's blocking (uses matplotlib/PIL under the hood).

**`log_manager.py`** sets up dual output (file + console). Console output is color-coded per logger name (spotify=green, discord=cyan, db=blue, discord.py internals=purple). Warnings/errors override color by severity. Log file opens in append mode.

## Docs maintenance

`README.md` and `docs/SETUP.md` are the human-facing docs — keep them up to date whenever
a change affects any of the following:

- **Dependencies** — `requirements.txt` or system packages (`ffmpeg`, `libopus0`, `davey`)
- **Credentials / config** — `.env.example` keys or their meaning
- **Commands** — new or removed slash commands, changed parameters
- **Features** — anything a user would read the README to discover

`CLAUDE.md` is agent-facing guidance; `docs/SETUP.md` is the user-readable setup guide.
They cover overlapping territory on purpose — update both when setup steps change.

## Code conventions

- Python 3.12, `snake_case` everywhere except `PascalCase` classes and `UPPER_CASE` module-level constants
- Max line length 100 characters (pylint enforced, must stay at 10.00/10)
- All source files live flat in `src/` — imports between modules use bare module names (e.g., `from db_manager import DBManager`), not package paths
- Settings/config for each manager is passed via a `@dataclass` (e.g., `SpotifyManagerSettings`, `DiscordManagerSettings`); bulk data passed between layers uses a `@dataclass` DTO (e.g., `MessageRecord`, `TrackData`)
- `storage/` directory (gitignored) is used for the SQLite DB and genre cloud PNGs; `DBManager` creates it on startup
