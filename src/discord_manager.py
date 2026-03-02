"""class wrapper for discord.py"""
import re
from dataclasses import dataclass, field
from logging import Logger
from typing import Any
from datetime import datetime
import discord
from utils import write_list_to_file, remove_query_params
from spotify_manager import SpotifyManager

@dataclass
class DiscordManagerSettings:
    """Different settings for initializing DiscordManager"""
    target_channel: str
    guild_id: int
    user_id: str
    logger: Logger
    options: dict[str, Any] = field(default_factory=dict)

class DiscordManager(discord.Client):
    """
    discord.py wrapper class
    built around discord.py
    """
    def __init__(
        self, spotify_manager: SpotifyManager, discord_settings: DiscordManagerSettings
    ):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents, **discord_settings.options)

        self.spotify_manager = spotify_manager
        self.target_channel = discord_settings.target_channel
        self.guild_id = int(discord_settings.guild_id)
        self.user_id = discord_settings.user_id

        self.logger = discord_settings.logger
        self.spotify_url_pattern = re.compile(r"(https?://open\.spotify\.com/[^\s]+)")

        self.tree = discord.app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        """Registers to the guild and updates the command list"""
        guild = discord.Object(id=self.guild_id)

        # register command manually in local tree scope
        @self.tree.command(
            name="help",
            description="Shows all available commands",
            guild=guild
        )
        async def help_command(interaction: discord.Interaction) -> None:
            await self._help_command(interaction)

        @self.tree.command(
            name="create_spotify_playlist",
            description="Collect Spotify URLs and create a playlist",
            guild=guild
        )
        async def create_spotify_playlist(
            interaction: discord.Interaction, limit: int | None = None
        ) -> None:
            await self._create_spotify_playlist(interaction, limit)

        # sync
        synced = await self.tree.sync(guild=guild)
        self.logger.info(f"Synced commands: {[s.name for s in synced]}")
        self.logger.info(f"Guild ID used: {self.guild_id}")

    async def on_ready(self) -> None:
        """Signals that the connection to discord is good"""
        self.logger.info(f"Connected guilds: {[g.id for g in self.guilds]}")

    async def _help_command(self, interaction: discord.Interaction) -> None:
        """Help Command

        Args:
            interaction (discord.Interaction): discord interaction
        """
        embed = discord.Embed(
            title="Bot Commands",
            description="Here are the available commands:",
            color=discord.Color.blurple()
        )

        embed.add_field(
            name="/create_spotify_playlist",
            value="Collect Spotify URLs into a singular playlist. "
            + "Collects spotify tracks, albums, and playlists. "
            + "Then stores said songs and playlist for later. "
            + "Necessary first step in many commands.",
            inline=False
        )

        embed.add_field(
            name="/help",
            value="Shows this help message",
            inline=False
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _create_spotify_playlist(
        self, interaction: discord.Interaction, limit: int | None
    ) -> None:
        """Internal logic for scanning Spotify URLs in the specified channel
        and collects them into a playlist.

        Args:
            interaction (discord.Interaction): interaction type
            limit (int | None): limit of messages to search
        """
        # Prevents webhook timeouts on large requests
        await interaction.response.defer()

        channel = interaction.channel
        spotify_urls = set()

        self.logger.info(
            f"Scanning channel '{channel}' (ID: {channel.id}) for Spotify URLs. Limit={limit}"
        )

        # Scan channel history
        async for message in channel.history(limit=limit):
            matches = self.spotify_url_pattern.findall(message.content)
            for url in matches:
                spotify_urls.add(remove_query_params(url))

        if not spotify_urls:
            self.logger.info("No Spotify URLs found.")
            await interaction.followup.send("No Spotify URLs found in this channel.")
            return

        # Write to file, using the user.id to store info for other commands
        write_list_to_file(spotify_urls, str(interaction.user.id) + ".dsm")

        playlist_name = f"Spotify jams from {self.get_guild(self.guild_id).name}"
        playlist_description = (
            f"Spotify jams from #{self.target_channel} in "
            f"{self.get_guild(self.guild_id).name} from "
            f"{datetime.today().strftime("%Y-%m-%d")}. "
            f"Created using discord-spotify-utility."
        )
        playlist = self.spotify_manager.create_playlist(playlist_name, playlist_description)
        self.logger.info(f"Created playlist: {playlist["external_urls"]["spotify"]}")

        self.spotify_manager.add_tracks_to_playlist(playlist["id"], spotify_urls)

        await interaction.followup.send(f"Finished compiling playlist: "
                                        f"{playlist["external_urls"]["spotify"]}")

        self.logger.info(f"Finished sending Spotify URL results, written to disk. "
                         f"Playlist URL: {playlist["external_urls"]["spotify"]}")
