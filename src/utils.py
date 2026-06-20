"""Assorted utility functions"""
import urllib.parse

def parse_spotify_url(url: str) -> tuple[str | None, str | None]:
    """Extract resource type and ID from a Spotify URL.
    e.g. https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC
         -> ('track', '4uLU6hMCjMI75M1A2tKUQC')
    """
    path_parts = url.rstrip("/").split("open.spotify.com/")[-1].split("/")
    if len(path_parts) >= 2:
        return path_parts[0], path_parts[1].split("?")[0]
    return None, None

def remove_query_params(url: str) -> str:
    """Removes URL query params from given string"""
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme + "://" + parsed.netloc + parsed.path
