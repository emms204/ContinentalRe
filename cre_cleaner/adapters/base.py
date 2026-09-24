"""Base adapter interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Tuple


class BaseAdapter(ABC):
    cedant: str = ""
    broker: str = ""

    @abstractmethod
    def discover_premium_files(self, raw_dir: Path, year: int, quarter: int) -> List[Tuple[int, Path]]:
        """Return list of (month_number, path) in calendar order."""

    @abstractmethod
    def discover_claims_files(self, raw_dir: Path, year: int, quarter: int) -> List[Path]:
        """Return quarterly claims bordereau paths."""
