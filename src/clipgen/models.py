"""Plain data types shared by every pipeline stage."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(slots=True)
class Word:
    start: float
    end: float
    text: str


@dataclass(slots=True)
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)


@dataclass(slots=True)
class Transcript:
    language: str
    source: str  # "captions" or "whisper"
    duration: float
    segments: list[Segment]

    @property
    def words(self) -> list[Word]:
        return [word for segment in self.segments for word in segment.words]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Transcript":
        segments = [
            Segment(
                start=s["start"],
                end=s["end"],
                text=s["text"],
                words=[Word(**w) for w in s["words"]],
            )
            for s in data["segments"]
        ]
        return cls(data["language"], data["source"], data["duration"], segments)


@dataclass(slots=True)
class Window:
    id: int
    start: float
    end: float
    text: str
    score: float = 0.0


@dataclass(slots=True)
class Clip:
    start: float
    end: float
    score: float
    title: str
    hook: str
    why: str
    tiktok_caption: str
    instagram_caption: str

    @property
    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict:
        return asdict(self)
