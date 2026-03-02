"""class wrapper for discord.py"""
import asyncio
import re
from dataclasses import dataclass, field
from logging import Logger
from typing import List, Any
from datetime import datetime
import discord
from utils import write_list_to_file, remove_query_params
from spotify_manager import SpotifyManager

@dataclass
class DiscordManagerSettings:
    """Different settings for initializing DiscordManager"""
    target_channel: str
    guild_ids: List[int]
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
        self.guild_ids = discord_settings.guild_ids
        self.user_id = discord_settings.user_id

        self.logger = discord_settings.logger
        self.spotify_url_pattern = re.compile(r"(https?://open\.spotify\.com/[^\s]+)")

        self.tree = discord.app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        """Registers to the guild and updates the command list"""
        for guild_id in self.guild_ids:
            guild = discord.Object(id=guild_id)

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
        self.logger.info(f"Guild IDs used: {self.guild_ids}")

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
        self, interaction: discord.Interaction, limit: int | None = 1000
    ) -> None:
        """Internal logic for scanning Spotify URLs in the specified channel
        and collects them into a playlist.

        Args:
            interaction (discord.Interaction): interaction type
            limit (int | None): limit of messages to search. Defaults to 1000.
        """
        # Prevents webhook timeouts on large requests
        await interaction.response.defer()
        channel = interaction.channel
        spotify_urls = set()
        self.logger.info(
            f"Scanning channel '{channel}' (ID: {channel.id}) for Spotify URLs. Limit={limit}"
        )

        message_count = 0
        async for message in channel.history(limit=limit):
            matches = self.spotify_url_pattern.findall(message.content)
            for url in matches:
                spotify_urls.add(remove_query_params(url))

            message_count += 1
            if message_count % 100 == 0:
                self.logger.info(f"Scanned {message_count} messages, "
                                 f"found {len(spotify_urls)} URLs so far...")
                await asyncio.sleep(1)

        if not spotify_urls:
            self.logger.info("No Spotify URLs found.")
            await interaction.followup.send("No Spotify URLs found in this channel.")
            return

        write_list_to_file(spotify_urls, str(interaction.user.id) + ".dsm")

        playlist_name = f"{interaction.guild.name} jams | DSU"
        playlist_description = (
            f"Spotify jams discord server {interaction.guild.name} compiled from "
            f"#{interaction.channel.name} at "
            f"{datetime.today().strftime('%Y-%m-%d')}. "
            f"Created using discord-spotify-utility. "
            "https://github.com/Forrest22/discord-spotify-utility"
        )

        loop = asyncio.get_event_loop()

        # Run blocking Spotify calls in a thread executor
        playlist = await loop.run_in_executor(
            None, self.spotify_manager.create_playlist, playlist_name, playlist_description
        )

        await loop.run_in_executor(
            None, self.spotify_manager.add_tracks_to_playlist, playlist["id"], spotify_urls
        )

        await interaction.followup.send(
            f"Finished compiling playlist: {playlist['external_urls']['spotify']}"
        )
        self.logger.info(f"Created playlist: {playlist['external_urls']['spotify']}")


