"""Keyboard constants for QWERTY-realistic typo simulation.

Ported from pydoll's `pydoll/constants.py` (MIT, autoscrape-labs):
  - QWERTY_NEIGHBORS: physically adjacent keys on a US QWERTY layout, used
    to pick a plausible "fat finger" mistype that lands on a neighbouring
    key rather than a random wrong char.
  - DEFAULT_TYPO_PROBABILITY: 2% per character — matches typical human
    typo rate on prose.
  - TYPO_WEIGHTS: relative likelihoods of the 5 typo categories
    (adjacent / transpose / double / skip / missed_space). Cloakbrowser
    only does adjacent; the other four are pydoll-specific and add a
    richer behavioural fingerprint.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, List


class TypoType(str, Enum):
    ADJACENT = "adjacent"           # press a neighbour key, then correct
    TRANSPOSE = "transpose"         # swap order of two chars, then correct both
    DOUBLE = "double"               # double-press a key, then backspace
    SKIP = "skip"                   # hesitate before typing the char
    MISSED_SPACE = "missed_space"   # type next char before space, then correct


DEFAULT_TYPO_PROBABILITY: float = 0.02

# Weights — must sum to 1.0 conceptually; passed to random.choices.
TYPO_WEIGHTS: Dict[TypoType, float] = {
    TypoType.ADJACENT: 0.55,
    TypoType.TRANSPOSE: 0.20,
    TypoType.DOUBLE: 0.12,
    TypoType.SKIP: 0.08,
    TypoType.MISSED_SPACE: 0.05,
}


# US QWERTY adjacency. Each entry lists physically neighbouring keys.
# Lowercase letters only — uppercase is handled by case-preserving the
# selected neighbour in the typo generator.
QWERTY_NEIGHBORS: Dict[str, List[str]] = {
    '1': ['2', 'q'],
    '2': ['1', '3', 'q', 'w'],
    '3': ['2', '4', 'w', 'e'],
    '4': ['3', '5', 'e', 'r'],
    '5': ['4', '6', 'r', 't'],
    '6': ['5', '7', 't', 'y'],
    '7': ['6', '8', 'y', 'u'],
    '8': ['7', '9', 'u', 'i'],
    '9': ['8', '0', 'i', 'o'],
    '0': ['9', '-', 'o', 'p'],
    '-': ['0', '=', 'p', '['],
    '=': ['-', '[', ']'],
    'q': ['1', '2', 'w', 'a', 's'],
    'w': ['q', '2', '3', 'e', 'a', 's', 'd'],
    'e': ['w', '3', '4', 'r', 's', 'd', 'f'],
    'r': ['e', '4', '5', 't', 'd', 'f', 'g'],
    't': ['r', '5', '6', 'y', 'f', 'g', 'h'],
    'y': ['t', '6', '7', 'u', 'g', 'h', 'j'],
    'u': ['y', '7', '8', 'i', 'h', 'j', 'k'],
    'i': ['u', '8', '9', 'o', 'j', 'k', 'l'],
    'o': ['i', '9', '0', 'p', 'k', 'l', ';'],
    'p': ['o', '0', '-', '[', 'l', ';', "'"],
    '[': ['p', '-', '=', ']', ';', "'"],
    ']': ['[', '=', "'"],
    'a': ['q', 'w', 's', 'z', 'x'],
    's': ['q', 'w', 'e', 'a', 'd', 'z', 'x', 'c'],
    'd': ['w', 'e', 'r', 's', 'f', 'x', 'c', 'v'],
    'f': ['e', 'r', 't', 'd', 'g', 'c', 'v', 'b'],
    'g': ['r', 't', 'y', 'f', 'h', 'v', 'b', 'n'],
    'h': ['t', 'y', 'u', 'g', 'j', 'b', 'n', 'm'],
    'j': ['y', 'u', 'i', 'h', 'k', 'n', 'm', ','],
    'k': ['u', 'i', 'o', 'j', 'l', 'm', ',', '.'],
    'l': ['i', 'o', 'p', 'k', ';', ',', '.', '/'],
    ';': ['o', 'p', '[', 'l', "'", '.', '/'],
    "'": ['p', '[', ']', ';', '/'],
    'z': ['a', 's', 'x'],
    'x': ['z', 'a', 's', 'd', 'c'],
    'c': ['x', 's', 'd', 'f', 'v'],
    'v': ['c', 'd', 'f', 'g', 'b'],
    'b': ['v', 'f', 'g', 'h', 'n'],
    'n': ['b', 'g', 'h', 'j', 'm'],
    'm': ['n', 'h', 'j', 'k', ','],
    ',': ['m', 'j', 'k', 'l', '.'],
    '.': [',', 'k', 'l', ';', '/'],
    '/': ['.', 'l', ';', "'"],
    ' ': ['c', 'v', 'b', 'n', 'm'],
}


# ASCII subset that has typos defined.
TYPOABLE_CHARS = frozenset(QWERTY_NEIGHBORS.keys()) | {ch.upper() for ch in QWERTY_NEIGHBORS if ch.isalpha()}
