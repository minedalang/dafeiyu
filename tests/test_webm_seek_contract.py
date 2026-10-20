"""Tests for the extracted WebM seek functions without needing a Qt GUI."""

import ast
import queue
import types
import unittest
from pathlib import Path


def _method(name):
    source = Path(__file__).resolve().parents[1] / "pet" / "webm_clip.py"
    parsed = ast.parse(source.read_text(encoding="utf-8"))
    cls = next(n for n in parsed.body if isinstance(n, ast.ClassDef)
               and n.name == "WebMClip")
    fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
    fn.decorator_list = []
    namespace = {
        "queue": queue,
        "mem_debug": types.SimpleNamespace(ENABLED=False),
        "perfstats": types.SimpleNamespace(ENABLED=False),
        "imageio_ffmpeg": object(),
    }
    module = ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[]))
    exec(compile(module, str(source), "exec"), namespace)
    return namespace[name]


class WebMSeekContractTests(unittest.TestCase):
    def test_nonzero_jump_restarts_at_requested_source_frame(self):
        jump = _method("jumpToFrame")

        class Stub:
            _running = True
            _cleaned = False
            _seek_start_frame = 0
            _frame_count = 240

            def __init__(self):
                self.stopped = False

            def _ensure_meta(self):
                pass

            def frameCount(self):
                return self._frame_count

            def stop(self):
                self.stopped = True

            def start(self):
                return True

        clip = Stub()
        self.assertTrue(jump(clip, 84))
        self.assertTrue(clip.stopped)
        self.assertEqual(clip._seek_start_frame, 84)

    def test_reader_labels_frames_from_seek_offset(self):
        stamp = _method("_stamp_source_indices")
        frames = [b"frame a", b"frame b", b"frame c"]
        got = queue.Queue()
        stamp(iter(frames), got, lambda: False, frame_offset=84)
        self.assertEqual([got.get_nowait()[1] for _ in frames], [84, 85, 86])


if __name__ == "__main__":
    unittest.main()
