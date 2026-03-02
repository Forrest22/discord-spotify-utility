"""class wrapper for discord.py"""
import re
from collections import defaultdict
import discord
from utils import write_list_to_file
from spotify_manager import SpotifyManager


class DiscordManagerSettings:
    """DiscordManagerSettings
    Different settings for initializing DiscordManager
    """
    def __init__(self, target_channel, guild_id, user_id, logger, **options):
        self.target_channel = target_channel
        self.guild_id = int(guild_id)
        self.user_id = user_id
        self.logger = logger
        self.options = options

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
        self.user_tracks = defaultdict(list)
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
            name="gather_spotify",
            description="Collect Spotify URLs and create a playlist",
            guild=guild
        )
        async def gather_spotify(
            interaction: discord.Interaction, limit: int | None = None
        ) -> None:
            await self._gather_spotify(interaction, limit)

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
            name="/gather_spotify",
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

    async def _gather_spotify(
        self, interaction: discord.Interaction, limit: int | None
    ) -> None:
        """Internal logic for scanning Spotify URLs

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
                spotify_urls.add(url)

        if not spotify_urls:
            self.logger.info("No Spotify URLs found.")
            await interaction.followup.send("No Spotify URLs found in this channel.")
            return

        # Deduplicate and sort
        url_list = sorted(spotify_urls)
        self.logger.info(f"Found {len(url_list)} Spotify URLs.")

        # Write to file, using the user.id to store info for other commands
        write_list_to_file(url_list, str(interaction.user.id) + ".dsm")

        self.spotify_manager.create_playlist()

        self.logger.info("Finished sending Spotify URL results, written to disk.")
