import pytest

from buyee_radar.core.keywords import Keyword


@pytest.fixture
def keywords():
    return [
        Keyword.build("Nike Division", search=["ナイキ"],
                      include=["ディビジョン", "division"], priority="high"),
        Keyword.build("Nike Trail", search=["ナイキ"], include=["トレイル", "trail"],
                      exclude=["シューズ", "sneakers"]),
        Keyword.build("Arcteryx", search=["アークテリクス"],
                      include=["アークテリクス", "arcteryx"], priority="low"),
    ]
