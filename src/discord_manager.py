# discord_manager.py
import re
import discord
from collections import defaultdict


class DiscordManager(discord.Client):
    def __init__(self, spotify_manager, target_channel, user_id, **options):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents, **options)

        self.spotify_manager = spotify_manager
        self.target_channel = target_channel
        self.user_id = user_id
        self.user_tracks = defaultdict(list)
        self.spotify_url_pattern = re.compile(
            r"https://open\.spotify\.com/track/([a-zA-Z0-9]+)"
        )

    async def on_ready(self):
        print(f"Logged in as {self.user}")

    async def on_message(self, message):
        print(f"on_message: {message}")
        if message.author == self.user:
            return

        if message.channel.name != self.target_channel:
            return

        matches = self.spotify_url_pattern.findall(message.content)
        print(matches)
        for track_id in matches:
            track_info = self.spotify_manager.get_track_info(track_id)
            self.user_tracks[message.author.name].append(track_info)

        # Check for the finalize command
        if message.content.strip() == "!finalize_playlist":
            print(f"Finalizing playlist...")
            await self.finalize_playlist(message.channel)

    async def finalize_playlist(self, channel):
        playlist_name = "Discord Collab Playlist"
        playlist_id = self.spotify_manager.create_playlist(self.user_id, playlist_name)

        all_track_ids = []
        for tracks in self.user_tracks.values():
            for track in tracks:
                all_track_ids.append(track["track_id"])

        self.spotify_manager.add_tracks_to_playlist(playlist_id, all_track_ids)

        # Create stats
        stats = "\n".join(
            [
                f"{user}: {len(tracks)} tracks"
                for user, tracks in self.user_tracks.items()
            ]
        )
        await channel.send(f"Playlist created! Here's the contribution stats:\n{stats}")
