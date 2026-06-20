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
from spotify_manager import SpotifyManager
from stats_manager import StatsManager
from utils import remove_query_params, parse_spotify_url
from viz import render_genre_cloud


@dataclass
class DiscordManagerSettings:
    """Settings for initializing DiscordManager"""
    target_channel: str
    guild_ids: List[int]
    user_id: str
    options: dict[str, Any] = field(default_factory=dict)


class DiscordManager(discord.Client):
    """
    discord.py wrapper class
    built around discord.py
    """
    SPOTIFY_URL_PATTERN = re.compile(r"(https?://open\.spotify\.com/[^\s]+)")
    _scan_lock = asyncio.Lock()

    def __init__(
        self,
        db: DBManager,
        spotify_manager: SpotifyManager,
        stats_manager: StatsManager,
        discord_settings: DiscordManagerSettings,
    ):
        self.logger = logging.getLogger("discord-spotify-util.discord")

        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents, **discord_settings.options)

        self.db = db
        self.spotify_manager = spotify_manager
        self.stats_manager = stats_manager
        self.discord_guilds = [
            discord.Object(id=guild_id) for guild_id in discord_settings.guild_ids
        ]
        self.user_id = discord_settings.user_id
        self.tree = discord.app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        """Registers slash commands and syncs them to all configured guilds."""

        @self.tree.command(name="help", description="Show all available bot commands")
        async def help_command(interaction: discord.Interaction) -> None:
            await self._help_command(interaction)

        @self.tree.command(
            name="create_spotify_playlist",
            description="Scan this channel for Spotify URLs and compile them into a playlist",
        )
        @discord.app_commands.describe(limit="Max messages to scan (default 1000, pass 0 for all)")
        async def create_spotify_playlist(
            interaction: discord.Interaction, limit: int = 1000
        ) -> None:
            actual_limit = limit if limit > 0 else None
            await self._create_spotify_playlist(interaction, actual_limit)

        @self.tree.command(
            name="sync_metadata",
            description="Enrich stored Spotify links with track, album, artist, and genre data",
        )
        async def sync_metadata(interaction: discord.Interaction) -> None:
            await self._sync_metadata_command(interaction)

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

    async def _create_spotify_playlist(
        self, interaction: discord.Interaction, limit: int | None = None
    ) -> None:
        """Scan the current channel for Spotify URLs and compile them into a playlist."""
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
                await self.__run_playlist_scan(interaction, limit)
            except discord.HTTPException as e:
                self.logger.exception("Discord API error in playlist scan: %s", e)
                await interaction.followup.send(
                    "❌ Discord API error — the bot may have lost channel permissions."
                )
            except spotipy.SpotifyException as e:
                self.logger.exception("Spotify API error in playlist scan: %s", e)
                await interaction.followup.send(
                    "❌ Spotify API error — check that the bot's Spotify token is still valid."
                )
            except SQLAlchemyError as e:
                self.logger.exception("Database error in playlist scan: %s", e)
                await interaction.followup.send(
                    "❌ Database error while recording messages."
                )

    async def __run_playlist_scan(
        self, interaction: discord.Interaction, limit: int | None
    ) -> None:
        """Core logic for the playlist scan (called while lock is held)."""
        channel = interaction.channel
        spotify_urls: set[str] = set()
        contributors: set[int] = set()
        message_count = 0
        url_counts: dict[str, int] = {"track": 0, "album": 0, "playlist": 0}

        self.logger.info("Scanning channel '%s' (ID: %s) for Spotify URLs. Limit=%s",
                         channel, channel.id, limit)

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

        async for message in channel.history(limit=limit):
            matches = self.SPOTIFY_URL_PATTERN.findall(message.content)
            for url in matches:
                clean = remove_query_params(url)
                spotify_urls.add(clean)
                rt = parse_spotify_url(clean)[0]
                if rt in url_counts:
                    url_counts[rt] += 1

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
            message_count += 1
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
                content="No Spotify URLs found in this channel."
            )
            return

        scan_counts = {
            "message_count": message_count,
            "contributors": len(contributors),
            "url_counts": url_counts,
        }
        await self.__post_playlist(interaction, spotify_urls, scan_counts)

    async def __post_playlist(
        self,
        interaction: discord.Interaction,
        spotify_urls: set[str],
        scan_counts: dict,
    ) -> None:
        """Create the Spotify playlist and post the completion embed."""
        playlist_name = f"{interaction.guild.name} jams | DSU"
        playlist_description = (
            f"Spotify jams from {interaction.guild.name} · "
            f"#{interaction.channel.name} · "
            f"{datetime.today().strftime('%Y-%m-%d')} · "
            "https://github.com/Forrest22/discord-spotify-utility"
        )
        await interaction.edit_original_response(content="🎵 Building playlist on Spotify…")
        loop = asyncio.get_running_loop()
        playlist = await loop.run_in_executor(
            None, self.spotify_manager.create_playlist, playlist_name, playlist_description
        )
        await loop.run_in_executor(
            None, self.spotify_manager.add_tracks_to_playlist, playlist["id"], spotify_urls
        )
        url_counts = scan_counts["url_counts"]
        embed = discord.Embed(
            title="✅ Playlist Created",
            url=playlist["external_urls"]["spotify"],
            color=discord.Color.green(),
        )
        embed.add_field(
            name="Messages scanned", value=f"{scan_counts['message_count']:,}", inline=True
        )
        embed.add_field(name="Links found", value=str(len(spotify_urls)), inline=True)
        embed.add_field(
            name="Contributors", value=str(scan_counts["contributors"]), inline=True
        )
        embed.add_field(
            name="Breakdown",
            value=(
                f"🎵 {url_counts['track']} tracks · "
                f"💿 {url_counts['album']} albums · "
                f"📋 {url_counts['playlist']} playlists"
            ),
            inline=False,
        )
        embed.add_field(
            name="Playlist", value=playlist["external_urls"]["spotify"], inline=False
        )
        await interaction.edit_original_response(content=None, embed=embed)
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
                emoji = "🎵"
            elif stat_type == "album":
                results = self.stats_manager.top_albums(period)  # type: ignore[arg-type]
                label = "Albums"
                emoji = "💿"
            else:
                results = self.stats_manager.top_artists(period)  # type: ignore[arg-type]
                label = "Artists"
                emoji = "🎤"
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
            title=f"{emoji} Top {label} — {period_label}",
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
            title=f"🎨 Genre Cloud — {period_label}",
            color=discord.Color.purple(),
        )
        embed.set_footer(text=f"Based on {sum(freqs.values()):,} genre-share data points")
        await interaction.followup.send(
            embed=embed, file=discord.File(str(out_path), filename="genre_cloud.png")
        )


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
