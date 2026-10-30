from __future__ import annotations

import csv
import hashlib
import os
import json
import struct
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable

DEFAULT_OSU_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "osu!"
CACHE_VERSION = 3

MODE_NAMES = {
    0: "osu!standard",
    1: "osu!taiko",
    2: "osu!catch",
    3: "osu!mania",
}
MODE_SHORT = {0: "STD", 1: "TAIKO", 2: "CATCH", 3: "MANIA"}

MOD_BITS = [
    (1, "NF"), (2, "EZ"), (4, "TD"), (8, "HD"), (16, "HR"),
    (32, "SD"), (64, "DT"), (128, "RX"), (256, "HT"), (512, "NC"),
    (1024, "FL"), (2048, "AT"), (4096, "SO"), (8192, "AP"),
    (16384, "PF"), (32768, "4K"), (65536, "5K"), (131072, "6K"),
    (262144, "7K"), (524288, "8K"), (1048576, "FI"), (2097152, "RD"),
    (4194304, "CM"), (8388608, "TP"), (16777216, "9K"),
    (33554432, "10K"), (536870912, "V2"), (1073741824, "MR"),
]


def mods_text(mods: int) -> str:
    if not mods:
        return "NM"
    labels: list[str] = []
    for bit, label in MOD_BITS:
        if mods & bit:
            labels.append(label)
    if "DT" in labels and "NC" in labels:
        labels.remove("DT")
    if "SD" in labels and "PF" in labels:
        labels.remove("SD")
    return " ".join(labels) if labels else "NM"


def mode_name(mode: int) -> str:
    return MODE_NAMES.get(mode, f"Mode {mode}")


def mode_short(mode: int) -> str:
    return MODE_SHORT.get(mode, "?")


def ticks_to_datetime(ticks: int) -> datetime:
    # osu! stable timestamps are .NET UTC ticks.
    value = datetime(1, 1, 1) + timedelta(microseconds=ticks / 10)
    try:
        return value.replace(tzinfo=timezone.utc).astimezone()
    except (OverflowError, ValueError):
        return value


@dataclass(slots=True)
class BeatmapInfo:
    md5: str
    path: str
    folder: str
    artist: str
    title: str
    creator: str
    version: str
    mode: int
    background: str
    audio: str
    star_rating: float = 0.0
    key_count: int = 0

    @classmethod
    def from_dict(cls, data: dict) -> "BeatmapInfo":
        return cls(
            md5=str(data.get("md5", "")),
            path=str(data.get("path", "")),
            folder=str(data.get("folder", "")),
            artist=str(data.get("artist", "")),
            title=str(data.get("title", "")),
            creator=str(data.get("creator", "")),
            version=str(data.get("version", "")),
            mode=int(data.get("mode", 0)),
            background=str(data.get("background", "")),
            audio=str(data.get("audio", "")),
            star_rating=float(data.get("star_rating", 0.0) or 0.0),
            key_count=int(data.get("key_count", 0) or 0),
        )

    @property
    def display_title(self) -> str:
        return self.title or Path(self.path).stem

    @property
    def display_artist(self) -> str:
        return self.artist or "未知艺术家"


@dataclass(slots=True)
class Score:
    mode: int
    version: int
    beatmap_md5: str
    player: str
    replay_md5: str
    n300: int
    n100: int
    n50: int
    geki: int
    katu: int
    miss: int
    score: int
    max_combo: int
    perfect: bool
    mods: int
    life: str
    timestamp_ticks: int
    replay_length: int
    online_id: int
    beatmap: BeatmapInfo | None = None

    @property
    def when(self) -> datetime:
        return ticks_to_datetime(self.timestamp_ticks)

    @property
    def accuracy(self) -> float:
        if self.mode == 3:
            total = (
                300 * (self.n300 + self.geki)
                + 200 * self.katu
                + 100 * self.n100
                + 50 * self.n50
            )
            maximum = 300 * (
                self.n300 + self.geki + self.katu
                + self.n100 + self.n50 + self.miss
            )
        elif self.mode == 1:
            total = 300 * self.n300 + 100 * self.n100
            maximum = 300 * (self.n300 + self.n100 + self.miss)
        else:
            total = 300 * self.n300 + 100 * self.n100 + 50 * self.n50
            maximum = 300 * (
                self.n300 + self.n100 + self.n50 + self.miss
            )
        return (total / maximum * 100.0) if maximum else 0.0

    @property
    def rank(self) -> str:
        acc = self.accuracy
        if acc >= 99.5 and self.miss == 0 and self.n100 == 0 and self.n50 == 0:
            return "SS"
        if acc >= 95.0:
            return "S"
        if acc >= 90.0:
            return "A"
        if acc >= 80.0:
            return "B"
        if acc >= 70.0:
            return "C"
        return "D"

    @property
    def mods_label(self) -> str:
        return mods_text(self.mods)


class BinaryReader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.offset = 0

    def u8(self) -> int:
        value = self.data[self.offset]
        self.offset += 1
        return value

    def u16(self) -> int:
        value = struct.unpack_from("<H", self.data, self.offset)[0]
        self.offset += 2
        return int(value)

    def i32(self) -> int:
        value = struct.unpack_from("<i", self.data, self.offset)[0]
        self.offset += 4
        return int(value)

    def i64(self) -> int:
        value = struct.unpack_from("<q", self.data, self.offset)[0]
        self.offset += 8
        return int(value)

    def f32(self) -> float:
        value = struct.unpack_from("<f", self.data, self.offset)[0]
        self.offset += 4
        return float(value)

    def f64(self) -> float:
        value = struct.unpack_from("<d", self.data, self.offset)[0]
        self.offset += 8
        return float(value)

    def string(self) -> str:
        present = self.u8()
        if present == 0:
            return ""
        length = 0
        shift = 0
        while True:
            byte = self.u8()
            length |= (byte & 0x7F) << shift
            if not (byte & 0x80):
                break
            shift += 7
        raw = self.data[self.offset:self.offset + length]
        self.offset += length
        return raw.decode("utf-8", errors="replace")


def parse_scores_db(path: Path) -> list[Score]:
    reader = BinaryReader(path.read_bytes())
    _version = reader.i32()
    beatmap_count = reader.i32()
    scores: list[Score] = []

    for _ in range(beatmap_count):
        beatmap_md5 = reader.string()
        score_count = reader.i32()
        for _ in range(score_count):
            mode = reader.u8()
            version = reader.i32()
            score_md5 = reader.string()
            player = reader.string()
            replay_md5 = reader.string()
            n300 = reader.u16()
            n100 = reader.u16()
            n50 = reader.u16()
            geki = reader.u16()
            katu = reader.u16()
            miss = reader.u16()
            total_score = reader.i32()
            max_combo = reader.u16()
            perfect = bool(reader.u8())
            mods = reader.i32()
            life = reader.string()
            timestamp_ticks = reader.i64()
            replay_length = reader.i32()
            online_id = reader.i64()

            scores.append(
                Score(
                    mode=mode,
                    version=version,
                    beatmap_md5=score_md5 or beatmap_md5,
                    player=player,
                    replay_md5=replay_md5,
                    n300=n300,
                    n100=n100,
                    n50=n50,
                    geki=geki,
                    katu=katu,
                    miss=miss,
                    score=total_score,
                    max_combo=max_combo,
                    perfect=perfect,
                    mods=mods,
                    life=life,
                    timestamp_ticks=timestamp_ticks,
                    replay_length=replay_length,
                    online_id=online_id,
                )
            )

    return scores


def _parse_osu_file(path: Path) -> BeatmapInfo:
    data = path.read_bytes()
    md5 = hashlib.md5(data).hexdigest()
    text = data.decode("utf-8-sig", errors="replace")

    section = ""
    metadata: dict[str, str] = {}
    general: dict[str, str] = {}
    difficulty: dict[str, str] = {}
    background = ""

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section == "Metadata" and ":" in line:
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip()
        elif section == "General" and ":" in line:
            key, value = line.split(":", 1)
            general[key.strip()] = value.strip()
        elif section == "Difficulty" and ":" in line:
            key, value = line.split(":", 1)
            difficulty[key.strip()] = value.strip()
        elif section == "Events" and line.startswith("0,"):
            try:
                parts = next(csv.reader([line]))
            except Exception:
                continue
            if len(parts) >= 3:
                candidate = parts[2].strip().strip('"')
                if candidate:
                    bg_path = path.parent / candidate
                    if bg_path.exists():
                        background = str(bg_path)
                        break

    mode = 0
    try:
        mode = int(general.get("Mode", "0"))
    except ValueError:
        pass

    artist = metadata.get("ArtistUnicode") or metadata.get("Artist") or "未知艺术家"
    title = metadata.get("TitleUnicode") or metadata.get("Title") or path.stem

    key_count = 0
    if mode == 3:
        try:
            key_count = int(round(float(difficulty.get("CircleSize", "0"))))
        except (TypeError, ValueError):
            key_count = 0

    return BeatmapInfo(
        md5=md5,
        path=str(path),
        folder=str(path.parent),
        artist=artist,
        title=title,
        creator=metadata.get("Creator", ""),
        version=metadata.get("Version", ""),
        mode=mode,
        background=background,
        audio=general.get("AudioFilename", ""),
        key_count=key_count,
    )


ProgressCallback = Callable[[int, int, str], None]


def load_osudb_star_ratings(path: Path) -> dict[str, tuple[int, float]]:
    """Read MD5 -> (mode, star rating) from osu!.db.

    osu!.db stores separate star rating maps for the four game modes.
    This parser only extracts the fields needed by the score wall and skips
    the rest of each record.
    """
    if not path.exists():
        return {}

    try:
        reader = BinaryReader(path.read_bytes())
        version = reader.i32()
        reader.i32()                 # folder count
        reader.u8()                  # account unlocked
        reader.i64()                 # unlock timestamp
        reader.string()              # player name
        beatmap_count = reader.i32()
        result: dict[str, tuple[int, float]] = {}

        for _ in range(beatmap_count):
            if version < 20191107:
                reader.i32()         # old entry size

            reader.string()          # artist
            reader.string()          # artist unicode
            reader.string()          # title
            reader.string()          # title unicode
            reader.string()          # creator
            reader.string()          # difficulty name
            reader.string()          # audio filename
            md5 = reader.string()
            reader.string()          # osu filename

            reader.u8()              # ranked status
            reader.u16()             # hit circles
            reader.u16()             # sliders
            reader.u16()             # spinners
            reader.i64()             # last modified time

            if version < 20140609:
                reader.u8(); reader.u8(); reader.u8(); reader.u8()
            else:
                reader.f32(); reader.f32(); reader.f32(); reader.f32()

            reader.f64()             # slider velocity

            star_maps: list[dict[int, float]] = []
            if version >= 20140609:
                for _mode in range(4):
                    pair_count = reader.i32()
                    pairs: dict[int, float] = {}
                    for _pair in range(pair_count):
                        reader.u8()  # .NET dictionary key marker
                        mods = reader.i32()
                        reader.u8()  # .NET dictionary value marker
                        rating = (
                            reader.f32()
                            if version > 20250107
                            else reader.f64()
                        )
                        pairs[mods] = float(rating)
                    star_maps.append(pairs)

            reader.i32()             # drain time
            reader.i32()             # total time
            reader.i32()             # preview time

            timing_count = reader.i32()
            for _ in range(timing_count):
                reader.f64()
                reader.f64()
                reader.u8()

            reader.i32()             # beatmap id
            reader.i32()             # beatmap set id
            reader.i32()             # thread id
            reader.u8(); reader.u8(); reader.u8(); reader.u8()

            reader.u16()             # local offset
            reader.f32()             # stack leniency
            mode = reader.u8()

            reader.string()          # source
            reader.string()          # tags
            reader.u16()             # online offset
            reader.string()          # title font

            reader.u8()              # unplayed
            reader.i64()             # last played
            reader.u8()              # osz2
            reader.string()          # folder name
            reader.i64()             # last checked
            reader.u8(); reader.u8(); reader.u8(); reader.u8(); reader.u8()

            if version < 20140609:
                reader.u16()
            reader.i32()             # last modified time 2
            reader.u8()              # mania scroll speed

            rating = 0.0
            if star_maps and 0 <= mode < len(star_maps):
                pairs = star_maps[mode]
                rating = float(
                    pairs.get(0, next(iter(pairs.values()), 0.0))
                )
            result[md5] = (int(mode), rating)

        return result
    except (OSError, struct.error, IndexError, ValueError):
        return {}


def scan_beatmaps(
    osu_dir: Path,
    progress: ProgressCallback | None = None,
) -> dict[str, BeatmapInfo]:
    songs_dir = osu_dir / "Songs"
    if not songs_dir.exists():
        raise FileNotFoundError(f"找不到 Songs 文件夹：{songs_dir}")

    files = sorted(songs_dir.rglob("*.osu"))
    total = len(files)
    result: dict[str, BeatmapInfo] = {}

    for index, path in enumerate(files, 1):
        try:
            info = _parse_osu_file(path)
            result[info.md5] = info
        except OSError:
            pass
        if progress is not None:
            progress(index, total, path.name)

    star_data = load_osudb_star_ratings(osu_dir / "osu!.db")
    for md5, (star_mode, rating) in star_data.items():
        info = result.get(md5)
        if info is not None:
            info.star_rating = rating
            if info.mode < 0:
                info.mode = star_mode

    return result


def save_cache(
    cache_path: Path,
    osu_dir: Path,
    beatmaps: dict[str, BeatmapInfo],
) -> None:
    payload = {
        "version": CACHE_VERSION,
        "osu_dir": str(osu_dir),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "beatmaps": {
            md5: asdict(info)
            for md5, info in beatmaps.items()
        },
    }
    cache_path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def load_cache(cache_path: Path, osu_dir: Path) -> dict[str, BeatmapInfo] | None:
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("version") != CACHE_VERSION:
        return None
    if payload.get("osu_dir") != str(osu_dir):
        return None
    beatmaps = payload.get("beatmaps", {})
    if not isinstance(beatmaps, dict):
        return None
    return {
        key: BeatmapInfo.from_dict(value)
        for key, value in beatmaps.items()
        if isinstance(value, dict)
    }


def attach_beatmaps(
    scores: list[Score],
    beatmaps: dict[str, BeatmapInfo],
) -> None:
    for score in scores:
        score.beatmap = beatmaps.get(score.beatmap_md5)
