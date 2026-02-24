"""Assorted utility functions"""
from typing import List


def write_to_file(l: List) -> None:
    """Writes a list to a file"""
    with open("output.txt", encoding="w") as file:
        for item in l:
            file.write(item + "\n")

def open_file(filename: str) -> List:
    """Reads a list from each line in file"""
    l = []
    with open(filename, encoding="r") as file:
        for line in file:
            l.append(line.rstrip())
