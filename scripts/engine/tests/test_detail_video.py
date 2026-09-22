"""Functional checks for direct recordings, readable pacing and timed annotations."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageChops

from product_video.common import VideoError, run, write_json
from product_video.compositor import Renderer
from product_video.config import load
from product_video.highlights import projected_rect
from product_video.pacing import assess
from product_video.pipeline import prepare
from product_video.shotcraft import build, timeline


class DetailVideoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='detail-video-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, color in [('before', '#862020'), ('after', '#205386')]:
            Image.new('RGB', (800, 500), color).save(self.root / f'{name}.png')
        self.movie = self.root / 'recording.mp4'
        run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=30',
             '-t', '5', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', self.movie])
        self.raw = {'schema_version': 2, 'product': {'name': 'Detail fixture'}, 'output': 'output',
            'voice': {'mode': 'none'}, 'video': {'width': 640, 'height': 360, 'fps': 30,
                'style': 'classic', 'subtitles': 'none', 'transition': 0, 'accent': '#00ff00'},
            'chapters': [{'id': 'flow', 'title': 'Flow', 'duration': 4, 'narration': '明确操作结果。',
                'steps': [{'at': 0, 'recording': {'source': 'recording.mp4', 'trim_start': 1, 'speed': 1},
                    'highlights': [{'rect': [.1, .1, .3, .2], 'start': .3, 'end': 2, 'label': 'Target'}]}]}]}

    def config(self, raw=None):
        path = self.root / 'project.json'
        write_json(path, raw or self.raw)
        return load(path)

    def test_recording_preserves_trim_speed_silence_and_local_annotation_seconds(self):
        from product_video.capture import resolve_images
        relocated = copy.deepcopy(self.raw)
        resolve_images(relocated, self.root, {})
        self.assertEqual(relocated['chapters'][0]['steps'][0]['recording']['source'], str(self.movie))
        self.raw['chapters'][0]['duration'] = 2
        self.raw['chapters'][0]['steps'][0]['recording']['speed'] = 2
        config = self.config()
        chapters = prepare(config, allow_api=False)
        project = timeline(config, chapters, self.root/'public', lambda *a: self.fail('No screenshot plate'))
        tracks = {t['id']: t['clips'] for t in project['tracks']}
        shot = tracks['shots'][0]
        self.assertEqual((shot['cardId'], shot['inOffset'], shot['speed'], shot['duration']), ('video-clip', 30, 2, 60))
        self.assertEqual((shot['props']['sourceWidth'], shot['props']['sourceHeight']), (640, 360))
        self.assertEqual(shot['props']['highlights'][0]['start'], .3)
        self.assertEqual(shot['props']['highlights'][0]['end'], 2)
        self.assertTrue(shot['props']['muted'])
        self.assertEqual(shot['props']['fit'], 'contain')
        self.assertEqual(tracks['narration'], [])
        self.assertEqual((self.root/'public'/shot['props']['file']).read_bytes(), self.movie.read_bytes())

    def test_recording_rejects_unavailable_source_interval_and_conflicting_visuals(self):
        for change in ({'trim_start': 5}, {'speed': 2}, {'trim_start': -1}, {'speed': 0}):
            raw = copy.deepcopy(self.raw)
            raw['chapters'][0]['steps'][0]['recording'].update(change)
            with self.assertRaises(VideoError):
                self.config(raw)
        raw = copy.deepcopy(self.raw)
        raw['chapters'][0]['steps'][0]['images'] = ['before.png']
        with self.assertRaisesRegex(VideoError, '独占'):
            self.config(raw)

    def test_highlight_bounds_and_local_timing_are_validated(self):
        for change in ({'rect': [.9, .1, .3, .2]}, {'rect': [0, 0, 0, .2]},
                       {'start': 2, 'end': 1}, {'end': 4.5}, {'style': 'invented'}):
            raw = copy.deepcopy(self.raw)
            raw['chapters'][0]['steps'][0]['highlights'][0].update(change)
            with self.assertRaises(VideoError):
                self.config(raw)

    def screenshot_config(self):
        raw = copy.deepcopy(self.raw)
        raw['video']['reduced_motion'] = True
        raw['chapters'][0]['steps'] = [
            {'at': 0, 'images': ['before.png']},
            {'at': .25, 'images': ['after.png'], 'transition': 'cut',
             'interaction': {'kind': 'click', 'from': [.8, .8], 'to': [.8, .5], 'move': .5, 'hold': .2, 'settle': .2},
             'highlights': [{'rect': [.1, .1, .3, .2], 'start': 0, 'end': 2, 'style': 'spotlight'}]}]
        return self.config(raw)

    def test_result_highlight_waits_for_click_and_sampling_includes_cause(self):
        config = self.screenshot_config()
        chapters = prepare(config, allow_api=False)
        with Renderer(config, chapters) as marked:
            plain_chapters = copy.deepcopy(chapters)
            plain_chapters[0]['steps'][1].pop('highlights')
            with Renderer(config, plain_chapters) as plain:
                self.assertIsNone(ImageChops.difference(marked.frame(1.4), plain.frame(1.4)).getbbox())
                self.assertIsNotNone(ImageChops.difference(marked.frame(2.2), plain.frame(2.2)).getbbox())
            times = marked.review_times(0, 1)
            self.assertTrue({'before', 'move', 'press', 'result', 'highlight-0'}.issubset(times))
            self.assertLess(times['press'], times['result'])
        self.assertEqual(projected_rect([.2, .3, .2, .3], [-100, -50, 1000, 600], [0, 0, 600, 400]),
                         (100, 130, 300, 310))

    def test_pacing_reports_specific_advice_without_changing_authored_timing(self):
        config = self.screenshot_config()
        config['video']['subtitles'] = 'auto'
        config['chapters'][0]['captions'] = [{'start': 0, 'end': .3, 'text': '这段字幕需要足够的时间阅读。'}]
        config['chapters'][0]['steps'][1]['at'] = .65
        config['chapters'][0]['steps'][1].pop('highlights')
        original = copy.deepcopy(config)
        chapters = prepare(config, allow_api=False)
        report = assess(config, chapters)
        self.assertEqual(config, original)
        self.assertFalse(report['timing_changed'])
        self.assertTrue({'caption-reading', 'result-hold'}.issubset({note['code'] for note in report['notes']}))
        self.assertTrue(all(note['chapter']=='flow' and note['suggestion'] for note in report['notes']))

    def test_silent_build_and_review_do_not_use_media_digest_helpers(self):
        from product_video.review import review
        config = self.screenshot_config()
        def render_fixture(action, request, folder):
            self.assertEqual(action, 'render')
            run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=640x360:r=30',
                 '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-t', '4', '-c:v', 'libx264',
                 '-pix_fmt', 'yuv420p', '-c:a', 'aac', request['output']])
        with patch.object(hashlib, 'sha256', side_effect=AssertionError('Media digest forbidden')), \
             patch('product_video.pipeline.generate', side_effect=AssertionError('TTS forbidden')), \
             patch('product_video.shotcraft.invoke', side_effect=render_fixture):
            folder = build(config, allow_api=False)
            report = json.loads((folder/'verification.json').read_text(encoding='utf-8'))
            self.assertEqual(report['full_decode'], 'passed')
            self.assertNotIn('sha256', report)
            points = json.loads((folder/'review-points.json').read_text(encoding='utf-8'))
            self.assertTrue({'move', 'press', 'result'}.issubset({point['phase'] for point in points}))
            with patch('product_video.review.extract_frames', return_value=[]) as extract:
                review(self.root/'project.json')
            self.assertTrue({point['frame'] for point in points}.issubset(extract.call_args.args[1]))


if __name__ == '__main__':
    unittest.main()
