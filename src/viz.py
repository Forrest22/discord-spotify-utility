"""Visualization utilities — renders charts to image files for Discord posting."""
from pathlib import Path
from wordcloud import WordCloud


def render_genre_cloud(freqs: dict[str, int], out_path: Path) -> Path:
    """Render a word cloud from genre frequencies and save to out_path as a PNG.

    Args:
        freqs: mapping of genre name → frequency/weight.
        out_path: destination file path (should end in .png).

    Returns:
        out_path, for convenience.
    """
    wc = WordCloud(
        width=900,
        height=450,
        background_color="black",
        colormap="Spectral",
        max_words=80,
        prefer_horizontal=0.8,
    ).generate_from_frequencies(freqs)
    wc.to_file(str(out_path))
    return out_path
