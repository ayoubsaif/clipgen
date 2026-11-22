import json
from pathlib import Path

import pytest
from PIL import Image

from clipgen import webcam
from clipgen.llm import ProviderError
from clipgen.models import Clip, Segment, Transcript, Word
from clipgen.pipeline import RunOptions
from clipgen.render import FACECAM_HOOK_MARGIN, build_ass
from clipgen.selection import SelectionOptions, select_clips


class FakeVision:
    def __init__(self, reply):
        self.reply = reply
        self.images = 0

    def complete_images(self, system, user, images, schema):
        self.images = len(images)
        return self.reply


def frames(folder: Path, count=2) -> list[Path]:
    paths = []
    for index in range(count):
        path = folder / f"frame-{index + 1}.jpg"
        Image.new("RGB", (1920, 1080), "navy").save(path)
        paths.append(path)
    return paths


def test_model_box_becomes_even_source_pixels():
    cam = webcam.to_pixels({"left": 100, "top": 500, "right": 301, "bottom": 801}, 1920, 1080)
    assert cam[0] == 192 and cam[1] == 540
    assert cam[2] % 2 == 0 and cam[3] % 2 == 0


@pytest.mark.parametrize("box", [
    {"left": 300, "top": 0, "right": 100, "bottom": 500},
    {"left": 0, "top": 0, "right": 1000, "bottom": 1000},
    {"left": 0, "top": 0, "right": 10, "bottom": 10},
    {"left": -5, "top": 0, "right": 300, "bottom": 300},
])
def test_impossible_boxes_are_rejected(box):
    with pytest.raises(ProviderError):
        webcam.to_pixels(box, 1920, 1080)


def test_manual_box_stays_inside_frame():
    x, y, width, height = webcam.fit_box((1900, 1000, 401, 301), 1920, 1080)
    assert x + width <= 1920 and y + height <= 1080 and width % 2 == 0 and height % 2 == 0


def test_detect_uses_the_chosen_frame(tmp_path):
    vision = FakeVision({"found": True, "frame": 2, "left": 0, "top": 500, "right": 250, "bottom": 800})
    found = webcam.detect(vision, frames(tmp_path))
    assert found["frame"] == 1 and found["cam"][0] == 0 and vision.images == 2


def test_detect_reports_no_webcam(tmp_path):
    vision = FakeVision({"found": False, "frame": 0, "left": 0, "top": 0, "right": 0, "bottom": 0})
    assert webcam.detect(vision, frames(tmp_path)) is None


def test_saved_position_skips_model(tmp_path):
    webcam.save_position(tmp_path, (10, 20, 300, 200), "manual")
    options = webcam.resolve_layout(tmp_path, tmp_path / "none.mp4", [], RunOptions(layout="facecam"),
                                    FakeVision({}), lambda _: None)
    assert options.cam == (10, 20, 300, 200) and options.layout == "facecam"


def test_no_webcam_falls_back_to_full_frame(tmp_path):
    folder = tmp_path / "webcam_frames"
    folder.mkdir()
    frames(folder)
    vision = FakeVision({"found": False, "frame": 0, "left": 0, "top": 0, "right": 0, "bottom": 0})
    options = webcam.resolve_layout(tmp_path, tmp_path / "none.mp4", [], RunOptions(layout="facecam"),
                                    vision, lambda _: None)
    assert options.layout == "blur" and options.cam is None and not (tmp_path / "webcam.json").exists()


def test_other_layouts_never_call_the_model(tmp_path):
    options = RunOptions(layout="blur")
    assert webcam.resolve_layout(tmp_path, tmp_path / "none.mp4", [], options, None, lambda _: None) is options


def test_preview_is_vertical(tmp_path):
    image = webcam.preview(frames(tmp_path, 1)[0], (0, 600, 480, 270), height=640)
    assert image.size == (360, 640)


@pytest.mark.parametrize("layout,margin", [("facecam", FACECAM_HOOK_MARGIN), ("blur", 0), ("center", 0)])
def test_hook_sits_below_webcam_only_in_facecam(layout, margin):
    clip = Clip(0.0, 10.0, 50.0, "Title", "My hook", "Why", "tt", "ig")
    hook = next(line for line in build_ass(clip, [], layout).splitlines() if ",Hook,," in line)
    assert hook.endswith(f",Hook,,0,0,{margin},,My hook")


def test_video_shorter_than_minimum_still_gets_a_clip():
    class Editor:
        name = "fake"

        def complete(self, system, user, schema):
            if "scores" in schema["properties"]:
                return {"scores": [{"id": 0, "score": 60}]}
            return {"clips": [{"start": 0.0, "end": 17.0, "score": 60, "title": "T", "hook": "H", "why": "W",
                               "tiktok_caption": "t", "instagram_caption": "i"}]}

    words = [Word(float(i), i + 0.8, f"word{i}") for i in range(17)]
    transcript = Transcript("es", "whisper", 17.58, [Segment(0.0, 16.8, " ".join(w.text for w in words), words)])
    clips = select_clips(transcript, Editor(), SelectionOptions(1, 20, 60), progress=lambda _: None)
    assert len(clips) == 1 and clips[0].start == 0.0 and clips[0].end >= 16.8


def test_corrupt_position_is_ignored(tmp_path):
    (tmp_path / "webcam.json").write_text(json.dumps({"cam": [1]}), encoding="utf-8")
    assert webcam.load_position(tmp_path) is None
