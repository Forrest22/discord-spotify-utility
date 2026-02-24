"""Discord Class Wrapper for discord.py"""
import re
from collections import defaultdict
import discord
from utils import write_to_file


class DiscordManager(discord.Client):
    """Discord Manager Class

    built around discord.py
    """
    def __init__(
        self, spotify_manager, target_channel, guild_id, user_id, logger, **options
    ):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents, **options)

        self.spotify_manager = spotify_manager
        self.target_channel = target_channel
        self.guild_id = int(guild_id)
        self.user_id = user_id
        self.user_tracks = defaultdict(list)
        self.logger = logger
        self.spotify_url_pattern = re.compile(r"(https?://open\.spotify\.com/[^\s]+)")

        self.tree = discord.app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        """Registers to the guild and updates the command list"""
        guild = discord.Object(id=self.guild_id)

        # register command manually
        @self.tree.command(
            name="gather_spotify", description="Collect Spotify URLs", guild=guild
        )
        async def gather_spotify(
            interaction: discord.Interaction, limit: int | None = None
        ):
            await self._gather_spotify(interaction, limit)

        # sync
        synced = await self.tree.sync(guild=guild)
        self.logger.info(f"Synced commands: {[s.name for s in synced]}")
        self.logger.info(f"Guild ID used: {self.guild_id}")

    async def on_ready(self) -> None:
        """Signals that the connection to discord is good"""
        self.logger.info(f"Connected guilds: {[g.id for g in self.guilds]}")

    async def _gather_spotify(
        self, interaction: discord.Interaction, limit: int | None
    ):
        """Internal logic for scanning Spotify URLs

        Args:
            interaction (discord.Interaction): interaction type
            limit (int | None): limit of messages to search
        """
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

        # Write to file
        write_to_file(url_list)

        # Chunk into groups of 10
        chunk_size = 10
        chunks = [
            url_list[i : i + chunk_size] for i in range(0, len(url_list), chunk_size)
        ]

        for chunk in chunks:
            await interaction.followup.send("\n".join(chunk))

        self.logger.info("Finished sending Spotify URL results.")
