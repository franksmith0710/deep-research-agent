from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SearchResultItem:
    title: str
    url: str
    snippet: str


class SearchEngine(ABC):
    @abstractmethod
    def search(self, query: str, num_results: int = 5) -> list[SearchResultItem]:
        ...
