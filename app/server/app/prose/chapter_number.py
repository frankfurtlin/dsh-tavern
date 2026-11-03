"""Candidate batch indices never become absolute reading chapter numbers."""
import re


def chapter_title(title, number):
    # Preserve a creative subtitle; only the ordinal belongs to the target line.
    match = re.search(r'第\s*[\d零〇一二三四五六七八九十百千两]+\s*章', title)
    if match:return title[:match.start()] + f'第 {number} 章' + title[match.end():]
    return title
