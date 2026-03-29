from __future__ import annotations

import logging
import re
from typing import Optional

import requests
from bs4 import BeautifulSoup, FeatureNotFound

logger = logging.getLogger(__name__)

USER_AGENT = "Stage2Cleaner/0.1"


def fetch_html(url: str, timeout: int = 10) -> Optional[str]:
    """Fetch HTML from a URL with a small timeout."""
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        resp.encoding = resp.encoding or resp.apparent_encoding
        return resp.text
    except Exception as exc:  # pragma: no cover - network errors are runtime concerns
        logger.error("Failed to fetch %s: %s", url, exc)
        return None


def extract_clean_text(html: str) -> str:
    """
    Extract readable text from HTML.

    Heuristics:
    - Strip obvious noise tags first.
    - Prefer <article>/<main> if present; otherwise pick the densest section/div.
    - Keep paragraph boundaries but collapse internal whitespace.
    """
    soup = _build_soup(html or "")
    _remove_noise_tags(soup)
    content_node = _choose_content_node(soup)
    paragraphs = _collect_paragraphs(content_node)
    if not paragraphs:
        raw_text = content_node.get_text("\n", strip=True)
        return _normalize_paragraphs(raw_text)
    return _normalize_paragraphs("\n\n".join(paragraphs))


def _build_soup(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except FeatureNotFound:
        return BeautifulSoup(html, "html.parser")


def _remove_noise_tags(soup: BeautifulSoup) -> None:
    for tag in soup(["script", "style", "noscript", "iframe", "header", "footer"]):
        tag.decompose()


def _choose_content_node(soup: BeautifulSoup):
    for tag_name in ("article", "main"):
        tag = soup.find(tag_name)
        if tag and _text_len(tag) > 200:
            return tag

    candidates = soup.find_all(["section", "div"])
    best = None
    best_score = 0
    for candidate in candidates:
        score = _density_score(candidate)
        if score > best_score:
            best = candidate
            best_score = score

    if best is not None:
        return best
    return soup.body or soup


def _collect_paragraphs(node) -> list[str]:
    paragraphs = []
    for tag in node.find_all(["p", "li", "h1", "h2", "h3"], recursive=True):
        text = tag.get_text(" ", strip=True)
        if not text:
            continue
        if len(text) < 15:
            continue
        paragraphs.append(text)
    return paragraphs


def _normalize_paragraphs(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in (text or "").splitlines()]
    kept = [line for line in lines if line]
    return "\n\n".join(kept)


def _text_len(node) -> int:
    return len(node.get_text(" ", strip=True))


def _density_score(node) -> int:
    text_length = _text_len(node)
    paragraph_count = len(node.find_all("p"))
    return text_length + paragraph_count * 20
