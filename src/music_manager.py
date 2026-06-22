"""Voice music playback: queue management and per-guild player state."""
import asyncio
import logging
import random
from collections import deque
from dataclasses import dataclass

import discord
import yt_dlp

from spotify_manager import SpotifyManager
from utils import parse_spotify_url


@dataclass
class MusicManagerSettings:
    """Settings for MusicManager."""
    dj_role_name: str = "Forrest approved DJ"
    loudness_i: float = -16.0


@dataclass
class QueuedTrack:
    """A single track held in the playback queue."""
    title: str
    search_query: str       # ytsearch1:… or a direct URL, resolved to audio at play time
    requested_by: str
    duration: int | None = None
    webpage_url: str | None = None


@dataclass
class ResolveResult:
    """Outcome of resolving a user query to one or more tracks."""
    tracks: list[QueuedTrack]
    description: str        # ready-to-display string for the confirmation embed


@dataclass
class GuildPlayer:
    """Per-guild voice connection and queue state."""
    voice_client: discord.VoiceClient
    queue: deque
    lock: asyncio.Lock      # guards queue + playback transitions
    current: QueuedTrack | None = None
    leaving: bool = False   # set during intentional stop so the after-callback doesn't re-advance


def _fmt_duration(seconds: int) -> str:
    """Format integer seconds as m:ss or h:mm:ss."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class MusicManager:
    """Manages per-guild voice connections, queues, and playback (excluding audio source)."""

    def __init__(self, spotify_manager: SpotifyManager, settings: MusicManagerSettings):
        self.logger = logging.getLogger("discord-spotify-util.music")
        self._spotify = spotify_manager
        self._settings = settings
        self._players: dict[int, GuildPlayer] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ydl_opts: dict = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "quiet": True,
            "default_search": "ytsearch",
        }

    # --- Resolution ---

    def resolve(self, query: str) -> ResolveResult:
        """Resolve a query (Spotify URL, YouTube URL, or text) to a ResolveResult.

        Blocking — call via run_in_executor.
        """
        resource_type, resource_id = parse_spotify_url(query)
        if resource_type and resource_id:
            return self._resolve_spotify(resource_type, resource_id)
        is_url = query.startswith("http://") or query.startswith("https://")
        return self._resolve_yt(query, is_url)

    def _resolve_spotify(self, resource_type: str, resource_id: str) -> ResolveResult:
        """Build tracks from Spotify metadata only — no yt-dlp at this stage."""
        pairs = self._spotify.get_search_queries(resource_type, resource_id)
        if not pairs:
            return ResolveResult(tracks=[], description="No tracks found.")

        tracks = [
            QueuedTrack(
                title=f"{title} — {artist}" if artist else title,
                search_query=f"ytsearch1:{title} {artist}".strip(),
                requested_by="",
            )
            for title, artist in pairs
        ]

        if resource_type == "track":
            title, artist = pairs[0]
            desc = f"**{title}**" + (f" — {artist}" if artist else "")
        else:
            name = self._spotify.get_resource_name(resource_type, resource_id)
            label = "top tracks" if resource_type == "artist" else "tracks"
            desc = f"**{name}** — {len(tracks)} {label}"

        return ResolveResult(tracks=tracks, description=desc)

    def _resolve_yt(self, query: str, is_url: bool) -> ResolveResult:
        """Resolve a YouTube URL or plain-text query via a single yt-dlp call."""
        search_query = query if is_url else f"ytsearch1:{query}"
        try:
            with yt_dlp.YoutubeDL(self._ydl_opts) as ydl:
                info = ydl.extract_info(search_query, download=False)
        except yt_dlp.utils.DownloadError as e:
            self.logger.warning("yt-dlp could not resolve '%s': %s", query, e)
            return ResolveResult(tracks=[], description="Could not find audio for that query.")

        if not info:
            return ResolveResult(tracks=[], description="No results found.")

        # ytsearch returns a playlist-shaped dict — unwrap to the first entry
        if "entries" in info:
            entries = list(info["entries"])
            info = entries[0] if entries else None

        if not info:
            return ResolveResult(tracks=[], description="No results found.")

        title = info.get("title", query)
        duration = info.get("duration")
        duration_str = f" ({_fmt_duration(duration)})" if duration else ""
        track = QueuedTrack(
            title=title,
            search_query=query if is_url else f"ytsearch1:{query}",
            requested_by="",
            duration=duration,
            webpage_url=info.get("webpage_url"),
        )
        return ResolveResult(tracks=[track], description=f"**{title}**{duration_str}")

    # --- Voice connection ---

    async def ensure_connected(self, channel: discord.VoiceChannel) -> GuildPlayer:
        """Connect to a voice channel, or move the bot if already connected elsewhere."""
        guild_id = channel.guild.id
        player = self._players.get(guild_id)
        if player and player.voice_client.is_connected():
            if player.voice_client.channel.id != channel.id:
                await player.voice_client.move_to(channel)
            return player
        vc = await channel.connect()
        new_player = GuildPlayer(
            voice_client=vc,
            queue=deque(),
            lock=asyncio.Lock(),
        )
        self._players[guild_id] = new_player
        return new_player

    # --- Queue operations ---

    def enqueue(
        self, guild_id: int, tracks: list[QueuedTrack], *, front: bool = False
    ) -> None:
        """Add tracks to the back (default) or front of the queue.

        front=True uses extendleft(reversed(…)) so a multi-track album stays
        in original order at the head of the queue.
        """
        player = self._players.get(guild_id)
        if not player:
            return
        if front:
            player.queue.extendleft(reversed(tracks))
        else:
            player.queue.extend(tracks)

    def shuffle(self, guild_id: int) -> None:
        """Randomly shuffle the pending queue without affecting the current track."""
        player = self._players.get(guild_id)
        if not player:
            return
        queue_list = list(player.queue)
        random.shuffle(queue_list)
        player.queue.clear()
        player.queue.extend(queue_list)

    @property
    def dj_role_name(self) -> str:
        """The Discord role name required to use music commands."""
        return self._settings.dj_role_name

    def clear(self, guild_id: int) -> None:
        """Empty the queue without stopping the current track."""
        player = self._players.get(guild_id)
        if not player:
            return
        player.queue.clear()

    # --- Playback control ---

    async def start(self, guild_id: int) -> None:
        """Begin playback if the player is currently idle."""
        await self._play_next(guild_id)

    def pause(self, guild_id: int) -> None:
        """Toggle pause/resume for the current track."""
        player = self._players.get(guild_id)
        if not player:
            return
        if player.voice_client.is_playing():
            player.voice_client.pause()
        elif player.voice_client.is_paused():
            player.voice_client.resume()

    async def stop(self, guild_id: int) -> None:
        """Stop playback, clear the queue, and disconnect from the voice channel."""
        player = self._players.get(guild_id)
        if not player:
            return
        async with player.lock:
            player.leaving = True
            player.queue.clear()
        player.voice_client.stop()   # fires after-callback; leaving=True makes it a no-op
        await player.voice_client.disconnect()
        self._players.pop(guild_id, None)

    # --- Private playback internals ---

    async def _play_next(self, guild_id: int) -> None:
        """Advance to the next queued track.

        Acquires the per-guild lock to guard queue mutations and playback
        transitions. Guards against double-advance and stop races via the
        is_playing/is_paused check and the leaving flag.
        """
        self._loop = asyncio.get_running_loop()
        player = self._players.get(guild_id)
        if not player:
            return

        track: QueuedTrack | None = None
        empty_channel = False
        async with player.lock:
            if player.leaving or not player.voice_client.is_connected():
                return
            if player.voice_client.is_playing() or player.voice_client.is_paused():
                return  # after-callback is already managing the queue
            humans = [m for m in player.voice_client.channel.members if not m.bot]
            if not humans:
                empty_channel = True
            elif not player.queue:
                player.current = None
            else:
                track = player.queue.popleft()
                player.current = track

        if empty_channel or track is None:
            # Leave: either no human listeners remain, or the queue is drained
            if empty_channel:
                self.logger.info("Voice channel empty in guild %s — leaving", guild_id)
            await player.voice_client.disconnect()
            self._players.pop(guild_id, None)
            return

        # Lazily resolve the stream URL outside the lock (blocking yt-dlp call)
        loop = asyncio.get_running_loop()
        try:
            stream_url = await loop.run_in_executor(
                None, self._get_stream_url, track.search_query
            )
        except Exception as err:  # pylint: disable=broad-except
            self.logger.error("Failed to resolve stream for '%s': %s", track.title, err)
            await self._play_next(guild_id)
            return

        # Guard against a /stop that arrived while we were resolving
        if player.leaving or not player.voice_client.is_connected():
            return

        loudness = self._settings.loudness_i
        before_opts = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
        audio_opts = f"-vn -af loudnorm=I={loudness}:TP=-1.5:LRA=11,aresample=48000"
        source = discord.FFmpegOpusAudio(
            stream_url, before_options=before_opts, options=audio_opts
        )

        def _after(err: Exception | None) -> None:
            if err:
                self.logger.error("Playback error in guild %s: %s", guild_id, err)
            if self._loop:
                asyncio.run_coroutine_threadsafe(self._play_next(guild_id), self._loop)

        player.voice_client.play(source, after=_after)
        self.logger.info("Now playing in guild %s: %s", guild_id, track.title)

    def _get_stream_url(self, search_query: str) -> str:
        """Resolve a search query to a direct audio stream URL. Blocking."""
        with yt_dlp.YoutubeDL(self._ydl_opts) as ydl:
            info = ydl.extract_info(search_query, download=False)
        if not info:
            raise ValueError(f"yt-dlp returned no info for: {search_query}")
        if "entries" in info:
            entries = list(info["entries"])
            info = entries[0] if entries else None
        if not info:
            raise ValueError(f"yt-dlp returned no entries for: {search_query}")
        url = info.get("url")
        if not url:
            raise ValueError(f"No stream URL in yt-dlp result for: {search_query}")
        return url
