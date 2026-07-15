"""ANSI colour helpers shared by the evaluation harnesses' terminal output.

Colour is off until ``set_colour(True)`` — callers enable it once at startup
(typically when stdout is a TTY and ``--no-colour`` was not passed).
"""

_COLOUR: bool = False


def set_colour(enabled: bool) -> None:
    """Turn ANSI colouring on or off for all helpers in this module."""
    global _COLOUR  # noqa: PLW0603
    _COLOUR = enabled


def _wrap(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOUR else text


def green(s: str) -> str:
    return _wrap("32", s)


def yellow(s: str) -> str:
    return _wrap("33", s)


def red(s: str) -> str:
    return _wrap("31", s)


def dim(s: str) -> str:
    return _wrap("2", s)


def colour_ratio(matched: int, total: int, text: str) -> str:
    """Colour a matched/total figure: red none, yellow partial, green full."""
    if matched == 0:
        return red(text)
    return green(text) if matched >= total else yellow(text)


def colour_score(score: float, text: str) -> str:
    """Colour a 0.0-1.0 score: green ≥0.8, yellow ≥0.4, red below."""
    if score >= 0.8:
        return green(text)
    return yellow(text) if score >= 0.4 else red(text)
