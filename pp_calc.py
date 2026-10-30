from __future__ import annotations

from pathlib import Path

from osu_data import Score

try:
    import rosu_pp_py as rosu
except ImportError:
    rosu = None


class PPCalculator:
    """Calculate estimated performance points for local scores with rosu-pp."""

    def __init__(self) -> None:
        self._difficulty_cache: dict[tuple[str, int], object] = {}

    @property
    def available(self) -> bool:
        return rosu is not None

    def calculate(self, score: Score) -> float | None:
        if rosu is None or score.beatmap is None:
            return None
        path = Path(score.beatmap.path)
        if not path.exists():
            return None

        key = (str(path), score.mods)
        try:
            if key not in self._difficulty_cache:
                beatmap = rosu.Beatmap(path=str(path))
                attributes = rosu.Difficulty(mods=score.mods).calculate(beatmap)
                self._difficulty_cache[key] = attributes
            attributes = self._difficulty_cache[key]
            performance = rosu.Performance(
                mods=score.mods,
                n_geki=score.geki,
                n_katu=score.katu,
                n300=score.n300,
                n100=score.n100,
                n50=score.n50,
                misses=score.miss,
                combo=score.max_combo,
                legacy_total_score=score.score,
                lazer=False,
            ).calculate(attributes)
            return float(performance.pp)
        except Exception:
            return None

    def stars(self, score: Score) -> float | None:
        if rosu is None or score.beatmap is None:
            return None
        path = Path(score.beatmap.path)
        if not path.exists():
            return None
        key = (str(path), score.mods)
        try:
            if key not in self._difficulty_cache:
                beatmap = rosu.Beatmap(path=str(path))
                attributes = rosu.Difficulty(mods=score.mods).calculate(beatmap)
                self._difficulty_cache[key] = attributes
            return float(self._difficulty_cache[key].stars)
        except Exception:
            return None

    def preview(
        self,
        beatmap_info,
        accuracy: float,
        mods: int = 0,
        combo_ratio: float = 0.95,
    ) -> tuple[float, float, int] | None:
        """Return (estimated pp, stars, max combo) for a target accuracy."""
        if rosu is None or beatmap_info is None:
            return None
        path = Path(beatmap_info.path)
        if not path.exists():
            return None
        key = (str(path), mods)
        try:
            if key not in self._difficulty_cache:
                beatmap = rosu.Beatmap(path=str(path))
                attributes = rosu.Difficulty(mods=mods).calculate(beatmap)
                self._difficulty_cache[key] = attributes
            attributes = self._difficulty_cache[key]
            combo = None
            if int(getattr(beatmap_info, "mode", 0)) != 3:
                combo = max(1, int(attributes.max_combo * combo_ratio))
            performance = rosu.Performance(
                mods=mods,
                accuracy=accuracy,
                combo=combo,
                lazer=False,
            ).calculate(attributes)
            return (
                float(performance.pp),
                float(attributes.stars),
                int(attributes.max_combo),
            )
        except Exception:
            return None
