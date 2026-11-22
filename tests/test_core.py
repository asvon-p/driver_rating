import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from local_runtime import STATE, environment

environment()
import av
from PIL import Image

from driver.scoring import append_segment, score
from driver.video import inspect_video, samples


class ScoreTest(unittest.TestCase):
    def calculate(self, parts):
        segments = []
        start = 0
        for duration, label in parts:
            append_segment(
                segments, start, start + duration, label, "confidence" if label is None else None
            )
            start += duration
        return score(segments, start)

    def test_plan_example(self):
        value = self.calculate([(36000, "c0"), (12000, "c1"), (6000, "c5"), (6000, None)])
        self.assertEqual((value["score"], value["coverage"]), (73.3, 90.0))

    def test_boundaries(self):
        self.assertEqual(self.calculate([(10000, "c0")])["score"], 100)
        self.assertEqual(self.calculate([(10000, "c1")])["score"], 0)
        self.assertIsNone(self.calculate([(9999, "c0")])["score"])
        self.assertEqual(self.calculate([(14000, "c0"), (6000, None)])["score"], 100)
        self.assertIsNone(self.calculate([(13999, "c0"), (6001, None)])["score"])
        self.assertEqual(self.calculate([(20000, None)])["coverage"], 0)
        self.assertIsNone(self.calculate([(20000, None)])["score"])

    def test_segment_boundaries(self):
        segments = []
        append_segment(segments, 0, 500, "c0")
        append_segment(segments, 500, 1000, "c0")
        append_segment(segments, 1000, 1123, reason="gap")
        self.assertEqual(len(segments), 2)
        self.assertEqual(score(segments, 1123)["valid_ms"], 1000)
        with self.assertRaises(ValueError):
            append_segment(segments, 1200, 1400, "c0")


class SamplingTest(unittest.TestCase):
    def test_variable_pts_and_partial_tail(self):
        with tempfile.TemporaryDirectory(dir=STATE / "tmp") as tmp:
            path = Path(tmp) / "vfr.mp4"
            timestamps = [0, 240, 510, 1260, 1501, 2550]
            with av.open(str(path), "w") as out:
                stream = out.add_stream("libx264", rate=25)
                stream.width, stream.height = 64, 64
                stream.pix_fmt = "yuv420p"
                stream.time_base = stream.codec_context.time_base = Fraction(1, 1000)
                stream.options = {"bf": "0"}
                for pts in timestamps:
                    frame = av.VideoFrame.from_image(Image.new("RGB", (64, 64)))
                    frame.pts, frame.time_base = pts, Fraction(1, 1000)
                    for packet in stream.encode(frame):
                        out.mux(packet)
                for packet in stream.encode(None):
                    out.mux(packet)
            duration, _ = inspect_video(path)
            intervals = list(samples(path, duration))
            self.assertEqual(
                [
                    round(frame.pts * frame.time_base * 1000) if frame else None
                    for _, _, frame in intervals
                ],
                [240, 510, 1260, 1501, None, 2550],
            )
            self.assertEqual(intervals[-1][1], duration)
            self.assertTrue(0 < intervals[-1][1] - intervals[-1][0] < 500)
            self.assertEqual(sum(end - start for start, end, _ in intervals), duration)
