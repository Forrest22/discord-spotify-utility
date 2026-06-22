"""Music trivia game mode: scoring, game state, and round management."""
import asyncio
import contextlib
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import discord

from db_manager import DBManager
from music_manager import MusicManager
from spotify_manager import SpotifyManager


# ---------------------------------------------------------------------------
# Pure scoring helpers
# ---------------------------------------------------------------------------

def normalize_words(text: str) -> set[str]:
    """Return a set of lowercased alphanumeric tokens from text."""
    return {w for w in re.sub(r"[^a-z0-9 ]", " ", text.lower()).split() if w}


def time_value(t: float, window: float = 60.0, grace: float = 5.0) -> int:
    """Points available for a guess arriving t seconds into the round.

    500 during the grace period; linear decay 500 → 100 after grace until window.
    """
    if t <= grace:
        return 500
    decay_frac = (t - grace) / max(1.0, window - grace)
    return max(100, round(500 - 400 * decay_frac))


def score_award(
    time_val: int,
    newly_claimed: int,
    total_words: int,
    current_score: int,
    per_song_cap: int = 500,
) -> int:
    """Points to award for claiming newly_claimed words when total_words exist.

    Award = time_val × (newly_claimed / total_words), rounded; at least 1 when
    words are claimed; capped so per-song total stays at per_song_cap.
    """
    if newly_claimed <= 0 or total_words <= 0:
        return 0
    raw = round(time_val * newly_claimed / total_words)
    award = max(1, raw)
    remaining = max(0, per_song_cap - current_score)
    return min(award, remaining)


# ---------------------------------------------------------------------------
# Settings and state dataclasses
# ---------------------------------------------------------------------------

@dataclass
class TriviaSettings:
    """Configuration for a trivia game."""
    genre: str = ""               # empty / "all" → broad cross-genre search
    window_seconds: float = 60.0
    grace_seconds: float = 5.0
    rounds: int = 5
    max_score: Optional[int] = None   # end game when any user reaches this
    end_mode: str = "time"            # "time" | "song_end"
    pool_size: int = 20               # how many tracks to pre-fetch from Spotify


class GuessResult:
    """Constants indicating the outcome kind of a trivia guess."""
    WRONG = "wrong"
    PARTIAL = "partial"
    CORRECT = "correct"


@dataclass
class GuessOutcome:
    """Rich result returned by submit_guess for the Discord layer to act on."""
    kind: str           # GuessResult constant
    username: str       # display name of the guesser
    points: int         # points awarded this guess (0 for wrong)
    newly_claimed: int  # how many new words were claimed
    total_words: int    # total words in the target pool


@dataclass
class TriviaRound:
    """Mutable state for a single trivia round."""
    target_words: set[str]
    total_words: int
    claimed: set[str]
    round_start: float
    round_scores: dict[int, int]    # user_id -> points this round
    last_guess: dict[int, float]    # user_id -> monotonic time of last guess


@dataclass
class TriviaSync:
    """Asyncio synchronization primitives for a trivia game."""
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    advance_event: asyncio.Event = field(default_factory=asyncio.Event)
    finished_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: Optional[asyncio.Task] = None
    stopping: bool = False


@dataclass
class TriviaProgress:
    """Per-game mutable progress (scores, usernames, round tracking)."""
    game_scores: dict[int, int] = field(default_factory=dict)  # user_id -> total pts
    usernames: dict[int, str] = field(default_factory=dict)    # user_id -> display name
    round_index: int = 0
    current_round: Optional[TriviaRound] = None


@dataclass
class TriviaGame:
    """Per-guild trivia game: static config, sync primitives, and progress."""
    guild_id: int
    channel_id: int
    voice_channel: discord.VoiceChannel
    settings: TriviaSettings
    pool: list[tuple[str, str]]     # (title, artist) pairs, pre-shuffled
    sync: TriviaSync = field(default_factory=TriviaSync)
    progress: TriviaProgress = field(default_factory=TriviaProgress)


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class TriviaManager:
    """Manages per-guild music trivia games."""

    COOLDOWN: float = 0.5  # seconds between accepted guesses per user

    def __init__(
        self,
        db: DBManager,
        spotify: SpotifyManager,
        music: MusicManager,
    ) -> None:
        self.logger = logging.getLogger("discord-spotify-util.trivia")
        self._db = db
        self._spotify = spotify
        self._music = music
        self._games: dict[int, TriviaGame] = {}

    def has_active_game(self, guild_id: int) -> bool:
        """Return True if a trivia game is running in this guild."""
        return guild_id in self._games

    async def start_game(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        voice_channel: discord.VoiceChannel,
        settings: TriviaSettings,
    ) -> Optional[str]:
        """Fetch song pool and launch the game loop as a background task.

        Returns an error string if the game cannot start, or None on success.
        genre is sourced from settings.genre.
        """
        guild_id = guild.id
        if guild_id in self._games:
            return "A trivia game is already running in this server."
        if self._music.is_active(guild_id):
            return "Stop the music player first before starting trivia."

        loop = asyncio.get_running_loop()
        pool: list[tuple[str, str]] = await loop.run_in_executor(
            None, self._spotify.get_genre_track_pool, settings.genre, settings.pool_size
        )
        if not pool:
            label = settings.genre or "all"
            return f"Couldn't find any tracks for genre '{label}'. Try a different genre."

        actual_rounds = min(settings.rounds, len(pool))
        if actual_rounds < settings.rounds:
            self.logger.info(
                "Trivia: only %s tracks for genre '%s'; capping at %s rounds.",
                len(pool), settings.genre, actual_rounds,
            )
        effective = TriviaSettings(
            genre=settings.genre,
            window_seconds=settings.window_seconds,
            grace_seconds=settings.grace_seconds,
            rounds=actual_rounds,
            max_score=settings.max_score,
            end_mode=settings.end_mode,
            pool_size=settings.pool_size,
        )

        await self._music.ensure_connected(voice_channel)

        game = TriviaGame(
            guild_id=guild_id,
            channel_id=channel.id,
            voice_channel=voice_channel,
            settings=effective,
            pool=pool,
        )
        self._games[guild_id] = game
        game.sync.task = asyncio.create_task(self._run_game(game, channel))
        return None

    async def skip(self, guild_id: int) -> None:
        """Advance to the next round immediately."""
        game = self._games.get(guild_id)
        if game:
            game.sync.advance_event.set()

    async def stop_game(self, guild_id: int) -> None:
        """End the game early."""
        game = self._games.get(guild_id)
        if game:
            game.sync.stopping = True
            game.sync.advance_event.set()

    async def submit_guess(self, message: discord.Message) -> Optional[GuessOutcome]:
        """Process a trivia guess; returns a GuessOutcome or None to ignore.

        Handles cooldown, normalization, word claiming, and scoring. The caller
        sends feedback (react ❌ on wrong; channel message on partial/correct).
        """
        game = self._games.get(message.guild.id)
        if not game or message.channel.id != game.channel_id:
            return None
        if game.sync.stopping or game.progress.current_round is None:
            return None
        return await self._process_guess(game, message)

    # --- Private round loop ---

    async def _process_guess(
        self, game: TriviaGame, message: discord.Message
    ) -> Optional[GuessOutcome]:
        """Score a validated guess against the current round. Returns GuessOutcome or None."""
        now = time.monotonic()
        rnd = game.progress.current_round
        elapsed = now - rnd.round_start
        if elapsed > game.settings.window_seconds:
            return None

        user_id = message.author.id
        username = message.author.display_name

        async with game.sync.lock:
            if now - rnd.last_guess.get(user_id, 0.0) < self.COOLDOWN:
                return None
            rnd.last_guess[user_id] = now

            game.progress.usernames[user_id] = username

            guess_words = normalize_words(message.content)
            newly = (guess_words & rnd.target_words) - rnd.claimed
            if not newly:
                return GuessOutcome(
                    kind=GuessResult.WRONG,
                    username=username, points=0,
                    newly_claimed=0, total_words=rnd.total_words,
                )

            rnd.claimed |= newly
            tval = time_value(
                elapsed, game.settings.window_seconds, game.settings.grace_seconds
            )
            current = rnd.round_scores.get(user_id, 0)
            award = score_award(tval, len(newly), rnd.total_words, current)
            if award > 0:
                rnd.round_scores[user_id] = current + award
                game.progress.game_scores[user_id] = (
                    game.progress.game_scores.get(user_id, 0) + award
                )

            fully_solved = rnd.claimed >= rnd.target_words
            kind = GuessResult.CORRECT if fully_solved else GuessResult.PARTIAL
            if fully_solved:
                game.sync.advance_event.set()
            return GuessOutcome(
                kind=kind,
                username=username,
                points=award,
                newly_claimed=len(newly),
                total_words=rnd.total_words,
            )

    async def _run_game(
        self, game: TriviaGame, channel: discord.TextChannel
    ) -> None:
        """Main game loop: iterate rounds, post results, persist scores."""
        try:
            for i in range(game.settings.rounds):
                if game.sync.stopping:
                    break
                if not self._humans_present(game):
                    await channel.send("Everyone left the voice channel — ending trivia.")
                    break
                game.progress.round_index = i
                await self._play_round(game, channel, i)
                if game.sync.stopping:
                    break
                if await self._max_score_reached(game, channel):
                    break

            await self._post_final_results(channel, game)
            await self._persist_scores(game)

        except Exception as err:  # pylint: disable=broad-except
            self.logger.error("Trivia game error in guild %s: %s", game.guild_id, err)
            await channel.send("An error occurred and the trivia game has ended.")
        finally:
            self._games.pop(game.guild_id, None)
            with contextlib.suppress(Exception):
                await self._music.stop_current(game.guild_id)

    async def _play_round(
        self, game: TriviaGame, channel: discord.TextChannel, idx: int
    ) -> None:
        """Set up, play, and score a single round."""
        title, artist = game.pool[idx]

        game.progress.current_round = TriviaRound(
            target_words=normalize_words(f"{title} {artist}"),
            total_words=0,
            claimed=set(),
            round_start=time.monotonic(),
            round_scores={},
            last_guess={},
        )
        game.progress.current_round.total_words = len(game.progress.current_round.target_words)
        game.sync.advance_event.clear()
        game.sync.finished_event.clear()

        await channel.send(
            f"**Round {idx + 1}/{game.settings.rounds}** — guess the title and artist!"
        )

        loop = asyncio.get_running_loop()
        finished_ev = game.sync.finished_event

        def _on_finished(
            _loop: asyncio.AbstractEventLoop = loop,
            _ev: asyncio.Event = finished_ev,
        ) -> None:
            _loop.call_soon_threadsafe(_ev.set)

        await self._music.play_track(
            game.guild_id, f"ytsearch1:{title} {artist}", on_finished=_on_finished
        )
        await self._wait_for_round_end(game)
        await self._music.stop_current(game.guild_id)
        await self._post_round_results(channel, game, (title, artist), idx + 1)

    async def _wait_for_round_end(self, game: TriviaGame) -> None:
        """Wait until the round ends (window expiry, song end, skip, or stop)."""
        sync = game.sync
        window = game.settings.window_seconds

        if game.settings.end_mode == "song_end":
            adv_task = asyncio.create_task(sync.advance_event.wait())
            fin_task = asyncio.create_task(sync.finished_event.wait())
            _, pending = await asyncio.wait(
                [adv_task, fin_task], return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await t
        else:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(sync.advance_event.wait(), timeout=window)

    async def _max_score_reached(
        self, game: TriviaGame, channel: discord.TextChannel
    ) -> bool:
        """Post a notice and return True if a player has hit the max_score threshold."""
        if not game.settings.max_score:
            return False
        leader = max(game.progress.game_scores.values(), default=0)
        if leader >= game.settings.max_score:
            await channel.send(f"Score limit of {game.settings.max_score} reached!")
            return True
        return False

    async def _post_round_results(
        self,
        channel: discord.TextChannel,
        game: TriviaGame,
        song: tuple[str, str],
        round_num: int,
    ) -> None:
        """Post the answer reveal and this round's scores."""
        title, artist = song
        rnd = game.progress.current_round
        lines = [f"Round {round_num} answer: **{title}** by **{artist}**"]
        if rnd and rnd.round_scores:
            lines.append("**Round scores:**")
            for uid, pts in sorted(rnd.round_scores.items(), key=lambda x: -x[1]):
                name = game.progress.usernames.get(uid, f"<@{uid}>")
                lines.append(f"  {name}: +{pts}")
        await channel.send("\n".join(lines))

    async def _post_final_results(
        self, channel: discord.TextChannel, game: TriviaGame
    ) -> None:
        """Post the final game leaderboard."""
        if not game.progress.game_scores:
            await channel.send("Game over! No points scored.")
            return
        lines = ["**Game over! Final scores:**"]
        for rank, (uid, pts) in enumerate(
            sorted(game.progress.game_scores.items(), key=lambda x: -x[1]), 1
        ):
            name = game.progress.usernames.get(uid, f"<@{uid}>")
            lines.append(f"  {rank}. {name}: {pts} pts")
        await channel.send("\n".join(lines))

    async def _persist_scores(self, game: TriviaGame) -> None:
        """Write each player's final game score to the database."""
        loop = asyncio.get_running_loop()
        for user_id, points in game.progress.game_scores.items():
            username = game.progress.usernames.get(user_id, str(user_id))
            try:
                await loop.run_in_executor(
                    None,
                    self._db.record_trivia_score,
                    user_id,
                    game.guild_id,
                    points,
                    username,
                )
            except Exception as err:  # pylint: disable=broad-except
                self.logger.error(
                    "Trivia: failed to persist score for user %s: %s", user_id, err
                )

    def _humans_present(self, game: TriviaGame) -> bool:
        """Return True if at least one non-bot member is in the voice channel."""
        return any(not m.bot for m in game.voice_channel.members)
