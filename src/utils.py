"""Assorted utility functions"""
from typing import List
import urllib.parse

def write_list_to_file(l: List, filename: str) -> None:
    """Writes a list to a file, temp until I implement a more permanent solution"""
    with open("storage/" + filename, "w", encoding="UTF-8") as file:
        for item in l:
            file.write(item + "\n")

def open_list_from_file(filename: str) -> List:
    """Reads a list from each line in file"""
    l = []
    with open("storage/" + filename, "r", encoding="UTF-8") as file:
        for line in file:
            l.append(line.rstrip())

def remove_query_params(url):
    """Removes URL query params from given string"""
    parsed_url = urllib.parse.urlparse(url)
    clean_url = parsed_url.scheme + "://" + parsed_url.netloc + parsed_url.path
    return clean_url