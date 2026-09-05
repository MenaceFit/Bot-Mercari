"""Fixtures communes aux tests de la nouvelle architecture."""

import time

import pytest

from snipe.core.keywords import Keyword
from snipe.sources.base import Listing, SearchQuery, SearchResult


@pytest.fixture
def keywords():
    return [
        Keyword.build("Nike Division", search=["ナイキ"],
                      include=["ディビジョン", "division"], priority="high"),
        Keyword.build("Nike Trail", search=["ナイキ"], include=["トレイル", "trail"],
                      exclude=["シューズ", "sneakers"]),
        Keyword.build("Under Armour", search=["アンダーアーマー"],
                      include=["アンダーアーマー", "under armour"], priority="low"),
    ]
