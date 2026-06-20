"""class wrapper for discord.py"""
import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Any, Literal

import discord
import spotipy
from sqlalchemy.exc import SQLAlchemyError

from db_manager import DBManager, MessageRecord
from music_manager import MusicManager
from spotify_manager import SpotifyManager
from stats_manager import StatsManager, period_cutoff
from utils import remove_query_params, parse_spotify_url
from viz import render_genre_cloud


class ConfirmView(discord.ui.View):
    """Confirmation prompt for /play and /playnext before tracks are queued."""

    def __init__(self, requester_id: int):
        super().__init__(timeout=60)
        self.requester_id = requester_id
        self.confirmed: bool = False
        self.message: discord.WebhookMessage | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Only the person who requested this can confirm or cancel.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        if self.message:
            await self.message.edit(content="Request timed out.", embed=None, view=None)

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Mark as confirmed and defer the button interaction."""
        # pylint: disable=unused-argument
        self.confirmed = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Stop the view and edit the message to show cancellation."""
        # pylint: disable=unused-argument
        self.stop()
        await interaction.response.edit_message(content="Cancelled.", embed=None, view=None)


@dataclass
class DiscordManagerDeps:
    """Collaborator managers injected into DiscordManager."""
    db: DBManager
    spotify_manager: SpotifyManager
    stats_manager: StatsManager
    music_manager: MusicManager


@dataclass
class DiscordManagerSettings:
    """Settings for initializing DiscordManager"""
    target_channel: str
    guild_ids: List[int]
    options: dict[str, Any] = field(default_factory=dict)


class DiscordManager(discord.Client):
    """
    discord.py wrapper class
    built around discord.py
    """
    SPOTIFY_URL_PATTERN = re.compile(r"(https?://open\.spotify\.com/[^\s]+)")
    _scan_lock = asyncio.Lock()

    def __init__(self, deps: DiscordManagerDeps, discord_settings: DiscordManagerSettings):
        self.logger = logging.getLogger("discord-spotify-util.discord")

        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        super().__init__(intents=intents, **discord_settings.options)

        self.db = deps.db
        self.spotify_manager = deps.spotify_manager
        self.stats_manager = deps.stats_manager
        self.music_manager = deps.music_manager
        self.discord_guilds = [
            discord.Object(id=guild_id) for guild_id in discord_settings.guild_ids
        ]
        self.tree = discord.app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        """Registers slash commands and syncs them to all configured guilds."""

        @self.tree.command(name="help", description="Show all available bot commands")
        async def help_command(interaction: discord.Interaction) -> None:
            await self._help_command(interaction)

        @self.tree.command(
            name="scan",
            description="Scan this channel for Spotify links and record them to the database",
        )
        @discord.app_commands.describe(
            limit="Max messages to scan (default 1000, pass 0 for all)",
            period="Only scan messages from this time period (default: all time)",
        )
        async def scan_command(
            interaction: discord.Interaction,
            limit: int = 1000,
            period: Literal["day", "week", "month", "year", "all"] = "all",
        ) -> None:
            actual_limit = limit if limit > 0 else None
            await self._scan(interaction, actual_limit, period)

        @self.tree.command(
            name="sync_metadata",
            description="Enrich stored Spotify links with track, album, artist, and genre data",
        )
        async def sync_metadata(interaction: discord.Interaction) -> None:
            await self._sync_metadata_command(interaction)

        @self.tree.command(
            name="build_playlist",
            description="Build a Spotify playlist from previously scanned links",
        )
        @discord.app_commands.describe(
            period="Only include links shared during this time period (default: all time)",
        )
        async def build_playlist_command(
            interaction: discord.Interaction,
            period: Literal["day", "week", "month", "year", "all"] = "all",
        ) -> None:
            await self._build_playlist(interaction, period)

        @self.tree.command(
            name="stats",
            description="Show the most popular songs, albums, or artists over a time period",
        )
        @discord.app_commands.describe(
            stat_type="What to rank",
            period="Time period to look back over",
        )
        async def stats_command(
            interaction: discord.Interaction,
            stat_type: Literal["song", "album", "artist"],
            period: Literal["day", "week", "month", "year", "all"] = "all",
        ) -> None:
            await self._stats_command(interaction, stat_type, period)

        @self.tree.command(
            name="genre_cloud",
            description="Post a word cloud of the most common genres shared in this server",
        )
        @discord.app_commands.describe(period="Time period to look back over")
        async def genre_cloud(
            interaction: discord.Interaction,
            period: Literal["day", "week", "month", "year", "all"] = "all",
        ) -> None:
            await self._genre_cloud_command(interaction, period)

        @self.tree.command(
            name="play",
            description="Play a song, album, playlist, or artist mix in your voice channel",
        )
        @discord.app_commands.describe(query="Spotify URL, YouTube URL, or search text")
        async def play_command(interaction: discord.Interaction, query: str) -> None:
            await self._play_or_next_command(interaction, query, front=False)

        @self.tree.command(
            name="playnext",
            description="Queue a track or album to play immediately after the current one",
        )
        @discord.app_commands.describe(query="Spotify URL, YouTube URL, or search text")
        async def playnext_command(interaction: discord.Interaction, query: str) -> None:
            await self._play_or_next_command(interaction, query, front=True)

        @self.tree.command(name="pause", description="Pause or resume the current track")
        async def pause_command(interaction: discord.Interaction) -> None:
            await self._pause_command(interaction)

        @self.tree.command(name="stop", description="Stop playback and disconnect the bot")
        async def stop_command(interaction: discord.Interaction) -> None:
            await self._stop_command(interaction)

        @self.tree.command(name="shuffle", description="Shuffle the current queue")
        async def shuffle_command(interaction: discord.Interaction) -> None:
            await self._shuffle_command(interaction)

        @self.tree.command(
            name="clear",
            description="Clear the queue without stopping the current track",
        )
        async def clear_command(interaction: discord.Interaction) -> None:
            await self._clear_command(interaction)

        for guild in self.discord_guilds:
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            self.logger.info("Synced commands %s to guild %s",
                             [s.name for s in synced], guild.id)

    async def on_ready(self) -> None:
        """Signals that the connection to Discord is established."""
        self.logger.info("Connected guilds: %s", [g.id for g in self.guilds])

    # --- Command implementations ---

    async def _help_command(self, interaction: discord.Interaction) -> None:
        """Auto-generate the help embed from the registered command tree."""
        embed = discord.Embed(
            title="Bot Commands",
            description="Here are the available commands:",
            color=discord.Color.blurple(),
        )
        for cmd in sorted(self.tree.get_commands(), key=lambda c: c.name):
            embed.add_field(name=f"/{cmd.name}", value=cmd.description, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _scan(
        self,
        interaction: discord.Interaction,
        limit: int | None = None,
        period: str = "all",
    ) -> None:
        """Scan the current channel for Spotify links and record them to the database."""
        if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "This command can only be used in a server text channel.", ephemeral=True
            )
            return

        if self._scan_lock.locked():
            await interaction.response.send_message(
                "A scan is already in progress — please wait for it to finish.", ephemeral=True
            )
            return

        await interaction.response.defer()

        async with self._scan_lock:
            try:
                await self.__run_scan(interaction, limit, period)
            except discord.HTTPException as e:
                self.logger.exception("Discord API error in scan: %s", e)
                await interaction.followup.send(
                    "❌ Discord API error — the bot may have lost channel permissions."
                )
            except SQLAlchemyError as e:
                self.logger.exception("Database error in scan: %s", e)
                await interaction.followup.send(
                    "❌ Database error while recording messages."
                )

    async def __run_scan(
        self, interaction: discord.Interaction, limit: int | None, period: str
    ) -> None:
        """Core scan logic: record link-bearing, non-bot messages (called while lock is held)."""
        channel = interaction.channel
        cutoff = period_cutoff(period)  # type: ignore[arg-type]
        spotify_urls: set[str] = set()
        contributors: set[int] = set()
        message_count = 0
        url_counts: dict[str, int] = {"track": 0, "album": 0, "playlist": 0}

        self.logger.info(
            "Scanning channel '%s' (ID: %s) for Spotify URLs. Limit=%s, period=%s",
            channel, channel.id, limit, period,
        )

        self.db.get_or_create_guild(
            guild_id=interaction.guild.id,
            name=interaction.guild.name,
            raw_data=_guild_to_dict(interaction.guild),
        )
        self.db.get_or_create_channel(
            channel_id=channel.id,
            guild_id=interaction.guild.id,
            name=channel.name,
            raw_data=_channel_to_dict(interaction.channel),
        )

        await interaction.edit_original_response(
            content=f"🔍 Scanning **#{channel.name}**… 0 messages scanned · 0 links found"
        )

        async for message in channel.history(limit=limit, after=cutoff):
            message_count += 1

            # Skip our own bot's messages
            if message.author.id == self.user.id:
                continue

            matches = self.SPOTIFY_URL_PATTERN.findall(message.content)
            if not matches:
                continue  # only record messages that contain at least one Spotify link

            self.__tally_urls(matches, url_counts, spotify_urls)

            contributors.add(message.author.id)
            self.db.get_or_create_discord_user(
                user_id=message.author.id,
                username=message.author.name,
                raw_data=_user_to_dict(message.author),
            )
            self.db.record_message(MessageRecord(
                message_id=message.id,
                channel_id=channel.id,
                author_id=message.author.id,
                content=message.content,
                created_at=message.created_at,
                raw_data=_message_to_dict(message),
                spotify_urls=[remove_query_params(u) for u in matches],
            ))

            if message_count % 100 == 0:
                self.logger.info(
                    "Scanned %s messages, %s URLs...", message_count, len(spotify_urls)
                )
                await interaction.edit_original_response(
                    content=(
                        f"🔍 Scanning **#{channel.name}**… "
                        f"{message_count:,} messages · {len(spotify_urls):,} links found"
                    )
                )
                await asyncio.sleep(1)

        if not spotify_urls:
            await interaction.edit_original_response(
                content="No Spotify links found in this channel for the selected period."
            )
            return

        scan_counts = {
            "message_count": message_count,
            "contributors": len(contributors),
            "url_counts": url_counts,
        }
        await self.__post_scan_summary(interaction, scan_counts, len(spotify_urls))

    def __tally_urls(
        self,
        matches: list[str],
        url_counts: dict[str, int],
        spotify_urls: set[str],
    ) -> None:
        """Clean and deduplicate matched URLs, adding them to spotify_urls and url_counts."""
        for url in matches:
            clean = remove_query_params(url)
            spotify_urls.add(clean)
            resource_type = parse_spotify_url(clean)[0]
            if resource_type in url_counts:
                url_counts[resource_type] += 1

    async def __post_scan_summary(
        self,
        interaction: discord.Interaction,
        scan_counts: dict,
        link_count: int,
    ) -> None:
        """Post the scan completion embed."""
        url_counts = scan_counts["url_counts"]
        embed = discord.Embed(title="✅ Scan Complete", color=discord.Color.green())
        embed.add_field(
            name="Messages scanned", value=f"{scan_counts['message_count']:,}", inline=True
        )
        embed.add_field(name="Links found", value=f"{link_count:,}", inline=True)
        embed.add_field(
            name="Contributors", value=str(scan_counts["contributors"]), inline=True
        )
        embed.add_field(
            name="Breakdown",
            value=(
                f"{url_counts['track']} tracks · "
                f"{url_counts['album']} albums · "
                f"{url_counts['playlist']} playlists"
            ),
            inline=False,
        )
        embed.set_footer(
            text="Run /sync_metadata to enrich, then /stats, /genre_cloud, or /build_playlist."
        )
        await interaction.edit_original_response(content=None, embed=embed)

    async def _build_playlist(
        self, interaction: discord.Interaction, period: str = "all"
    ) -> None:
        """Build a Spotify playlist from previously scanned links for this channel."""
        if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                "This command can only be used in a server text channel.", ephemeral=True
            )
            return

        await interaction.response.defer()

        cutoff = period_cutoff(period)  # type: ignore[arg-type]
        loop = asyncio.get_running_loop()
        try:
            urls = await loop.run_in_executor(
                None,
                self.db.get_spotify_urls_for_channel,
                interaction.channel.id,
                cutoff,
            )
        except SQLAlchemyError as e:
            self.logger.exception("Database error fetching URLs for playlist: %s", e)
            await interaction.followup.send("❌ Database error while fetching scanned links.")
            return

        if not urls:
            period_label = period.capitalize() if period != "all" else "all time"
            await interaction.followup.send(
                f"No scanned links found for this channel ({period_label}). "
                "Run `/scan` first to record Spotify links."
            )
            return

        playlist_name = f"{interaction.guild.name} jams | DSU"
        playlist_description = (
            f"Spotify jams from {interaction.guild.name} · "
            f"#{interaction.channel.name} · "
            f"{datetime.today().strftime('%Y-%m-%d')} · "
            "https://github.com/Forrest22/discord-spotify-utility"
        )
        try:
            playlist = await loop.run_in_executor(
                None, self.spotify_manager.create_playlist, playlist_name, playlist_description
            )
            await loop.run_in_executor(
                None, self.spotify_manager.add_tracks_to_playlist, playlist["id"], urls
            )
        except spotipy.SpotifyException as e:
            self.logger.exception("Spotify API error building playlist: %s", e)
            await interaction.followup.send(
                "❌ Spotify API error — check that the bot's Spotify token is still valid."
            )
            return

        embed = discord.Embed(
            title="✅ Playlist Created",
            url=playlist["external_urls"]["spotify"],
            color=discord.Color.green(),
        )
        embed.add_field(name="Links added", value=f"{len(urls):,}", inline=True)
        embed.add_field(
            name="Playlist", value=playlist["external_urls"]["spotify"], inline=False
        )
        await interaction.followup.send(embed=embed)
        self.logger.info("Created playlist: %s", playlist["external_urls"]["spotify"])

    async def _sync_metadata_command(self, interaction: discord.Interaction) -> None:
        """Enrich stored Spotify links with track, artist, album, and genre data."""
        await interaction.response.defer()
        await interaction.edit_original_response(
            content="🔄 Syncing metadata… this may take a few minutes for large channels."
        )
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(None, self.spotify_manager.sync_metadata)
        except spotipy.SpotifyException as e:
            self.logger.exception("Spotify error during sync: %s", e)
            await interaction.edit_original_response(
                content="❌ Spotify API error during sync — check the bot's token."
            )
            return
        except SQLAlchemyError as e:
            self.logger.exception("Database error during sync: %s", e)
            await interaction.edit_original_response(content="❌ Database error during sync.")
            return

        embed = discord.Embed(title="✅ Metadata Sync Complete", color=discord.Color.green())
        embed.add_field(name="Links processed", value=str(result["links_processed"]), inline=True)
        embed.add_field(name="Track shares", value=str(result["track_shares"]), inline=True)
        embed.add_field(name="Unique tracks", value=str(result["unique_tracks"]), inline=True)
        embed.add_field(name="Unique artists", value=str(result["unique_artists"]), inline=True)
        embed.add_field(name="Unique albums", value=str(result["unique_albums"]), inline=True)
        await interaction.edit_original_response(content=None, embed=embed)

    async def _stats_command(
        self,
        interaction: discord.Interaction,
        stat_type: str,
        period: str,
    ) -> None:
        """Show top tracks, albums, or artists for the given time period."""
        await interaction.response.defer()
        try:
            if stat_type == "song":
                results = self.stats_manager.top_tracks(period)  # type: ignore[arg-type]
                label = "Songs"
            elif stat_type == "album":
                results = self.stats_manager.top_albums(period)  # type: ignore[arg-type]
                label = "Albums"
            else:
                results = self.stats_manager.top_artists(period)  # type: ignore[arg-type]
                label = "Artists"
        except SQLAlchemyError as e:
            self.logger.exception("DB error in stats: %s", e)
            await interaction.followup.send("❌ Database error fetching stats.")
            return

        if not results:
            await interaction.followup.send(
                f"No data found for **{label}** · **{period}**. "
                "Run `/sync_metadata` first to enrich stored links."
            )
            return

        period_label = period.capitalize() if period != "all" else "All time"
        embed = discord.Embed(
            title=f"Top {label} — {period_label}",
            color=discord.Color.blurple(),
        )
        lines = []
        for rank, (name, count) in enumerate(results, start=1):
            lines.append(f"**{rank}.** {name} — {count:,} share{'s' if count != 1 else ''}")
        embed.description = "\n".join(lines)
        await interaction.followup.send(embed=embed)

    async def _genre_cloud_command(
        self, interaction: discord.Interaction, period: str
    ) -> None:
        """Post a genre word cloud PNG for the given time period."""
        await interaction.response.defer()
        try:
            freqs = self.stats_manager.genre_frequencies(period)  # type: ignore[arg-type]
        except SQLAlchemyError as e:
            self.logger.exception("DB error fetching genres: %s", e)
            await interaction.followup.send("❌ Database error fetching genre data.")
            return

        if not freqs:
            await interaction.followup.send(
                "No genre data found. Run `/sync_metadata` first to populate artist genres."
            )
            return

        out_path = Path(__file__).parent.parent / "storage" / f"genre_cloud_{period}.png"
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, render_genre_cloud, freqs, out_path)

        period_label = period.capitalize() if period != "all" else "All time"
        embed = discord.Embed(
            title=f"Genre Cloud — {period_label}",
            color=discord.Color.purple(),
        )
        embed.set_footer(text=f"Based on {sum(freqs.values()):,} genre-share data points")
        await interaction.followup.send(
            embed=embed, file=discord.File(str(out_path), filename="genre_cloud.png")
        )


    # --- Music command implementations ---

    async def __check_music_perms(self, interaction: discord.Interaction) -> bool:
        """Return True if the member may use music commands; send an ephemeral error otherwise."""
        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message(
                "Music commands can only be used in a server.", ephemeral=True
            )
            return False
        dj_role = self.music_manager.dj_role_name
        if not discord.utils.get(member.roles, name=dj_role):
            await interaction.response.send_message(
                f"You need the **{dj_role}** role to use music commands.",
                ephemeral=True,
            )
            return False
        if not member.voice or not member.voice.channel:
            await interaction.response.send_message(
                "You need to be in a voice channel to use music commands.", ephemeral=True
            )
            return False
        guild_vc = interaction.guild.voice_client if interaction.guild else None
        if guild_vc and member.voice.channel.id != guild_vc.channel.id:
            await interaction.response.send_message(
                "You need to be in the same voice channel as the bot.", ephemeral=True
            )
            return False
        return True

    async def _play_or_next_command(
        self, interaction: discord.Interaction, query: str, front: bool
    ) -> None:
        """Shared handler for /play and /playnext."""
        if not await self.__check_music_perms(interaction):
            return
        await interaction.response.defer()
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, self.music_manager.resolve, query)

        if not result.tracks:
            await interaction.followup.send(f"No results found for **{query}**.")
            return

        embed = discord.Embed(description=result.description, color=discord.Color.blurple())
        embed.set_footer(text=f"{len(result.tracks)} track(s) · Confirm to queue")
        view = ConfirmView(requester_id=interaction.user.id)
        msg = await interaction.followup.send(embed=embed, view=view)
        view.message = msg

        await view.wait()
        if not view.confirmed:
            return  # cancel or timeout already edited the message

        member = interaction.user
        if not isinstance(member, discord.Member) or not member.voice or not member.voice.channel:
            await interaction.followup.send(
                "You're no longer in a voice channel.", ephemeral=True
            )
            return

        for track in result.tracks:
            track.requested_by = member.display_name

        guild_id = interaction.guild_id
        await self.music_manager.ensure_connected(member.voice.channel)
        self.music_manager.enqueue(guild_id, result.tracks, front=front)
        await self.music_manager.start(guild_id)

        if len(result.tracks) == 1:
            added = f"✅ Added **{result.tracks[0].title}** to the queue."
        else:
            added = f"✅ Added {len(result.tracks)} tracks to the queue."
        await msg.edit(content=added, embed=None, view=None)

    async def _pause_command(self, interaction: discord.Interaction) -> None:
        """Toggle pause/resume for the current track."""
        if not await self.__check_music_perms(interaction):
            return
        guild_vc = interaction.guild.voice_client if interaction.guild else None
        is_playing = guild_vc is not None and guild_vc.is_playing()
        self.music_manager.pause(interaction.guild_id)
        label = "Paused." if is_playing else "Resumed."
        await interaction.response.send_message(label, ephemeral=True)

    async def _stop_command(self, interaction: discord.Interaction) -> None:
        """Stop playback and disconnect the bot."""
        if not await self.__check_music_perms(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        await self.music_manager.stop(interaction.guild_id)
        await interaction.followup.send("Stopped playback and disconnected.")

    async def _shuffle_command(self, interaction: discord.Interaction) -> None:
        """Shuffle the current queue."""
        if not await self.__check_music_perms(interaction):
            return
        self.music_manager.shuffle(interaction.guild_id)
        await interaction.response.send_message("Queue shuffled.", ephemeral=True)

    async def _clear_command(self, interaction: discord.Interaction) -> None:
        """Clear the queue without stopping the current track."""
        if not await self.__check_music_perms(interaction):
            return
        self.music_manager.clear(interaction.guild_id)
        await interaction.response.send_message("Queue cleared.", ephemeral=True)

    # --- Discord object serialisation helpers ---

def _guild_to_dict(guild: discord.Guild) -> dict:
    return {
        "id": guild.id,
        "name": guild.name,
        "icon": str(guild.icon) if guild.icon else None,
        "owner_id": guild.owner_id,
        "member_count": guild.member_count,
    }

def _channel_to_dict(channel: discord.TextChannel) -> dict:
    return {
        "id": channel.id,
        "name": channel.name,
        "guild_id": channel.guild.id,
        "topic": channel.topic,
        "nsfw": channel.nsfw,
        "position": channel.position,
    }

def _user_to_dict(user: discord.User | discord.Member) -> dict:
    return {
        "id": user.id,
        "username": user.name,
        "discriminator": user.discriminator,
        "bot": user.bot,
        "avatar": str(user.avatar) if user.avatar else None,
    }

def _message_to_dict(message: discord.Message) -> dict:
    return {
        "id": message.id,
        "content": message.content,
        "created_at": message.created_at.isoformat(),
        "edited_at": message.edited_at.isoformat() if message.edited_at else None,
        "author_id": message.author.id,
        "channel_id": message.channel.id,
        "guild_id": message.guild.id if message.guild else None,
        "pinned": message.pinned,
        "tts": message.tts,
        "mention_everyone": message.mention_everyone,
    }
