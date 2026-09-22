"""Behavioral regression checks for Windows portability and subtitle-only work."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from product_video import credentials
from product_video.capture import read_plan
from product_video.cli import execute, parser
from product_video.common import VideoError, atomic_write, write_json
from product_video.config import load
from product_video.pipeline import prepare, project_lock, master_audio
from product_video.shotcraft import timeline
from product_video.subtitles import from_text, srt
from product_video.windows_capture import _validated_target, _choose_window
from product_video.tts import cache_location, DEFAULT_VOICE


class SilentProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='product-video-中文 space-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / '项目 文件.json'
        self.raw = {'schema_version': 2, 'product': {'name': '字幕示例'}, 'output': '输出 video',
            'voice': {'mode': 'none'}, 'chapters': [
            {'id': 'intro', 'title': '总览', 'narration': '先看案件。再看待办。', 'duration': 4,
             'captions': [{'start': 0, 'end': 2, 'text': '先看案件。'}, {'start': 2, 'end': 4, 'text': '再看待办。'}],
             'steps': [{'at': 0, 'shotcraft': {'style': 'row-embed'}}]},
            {'id': 'next', 'title': '下一步', 'narration': '打开已有工作。', 'duration': 2,
             'steps': [{'at': 0, 'shotcraft': {'style': 'row-embed'}}]}]}
        write_json(self.path, self.raw)

    def test_explicit_silent_duration_has_no_voice_or_lead_pause(self):
        config = load(self.path)
        with patch('product_video.pipeline.generate', side_effect=AssertionError('TTS forbidden')), \
             patch('product_video.pipeline.cached_audio', side_effect=AssertionError('No voice cache')), \
             patch('product_video.pipeline.cache_location', side_effect=AssertionError('No voice cache')):
            chapters = prepare(config, allow_api=True)
        self.assertEqual([(c['start'], c['end']) for c in chapters], [(0, 4), (4, 6)])
        self.assertTrue(all(c['audio'] is None for c in chapters))
        self.assertEqual(chapters[1]['cues'][0]['start'], 4)
        self.assertIn('not speech alignment', chapters[1]['caption_alignment'])
        self.assertIn('00:00:04,000 --> 00:00:06,000', srt([q for c in chapters for q in c['cues']]))
        project = timeline(config, chapters, self.root / 'public', lambda *a: self.fail('No legacy plate'))
        tracks = {t['id']: t['clips'] for t in project['tracks']}
        self.assertEqual(tracks['narration'], [])
        self.assertEqual(len(tracks['captions']), 3)
        self.assertEqual(tracks['shots'][-1]['start'] + tracks['shots'][-1]['duration'], 180)

    def test_auto_never_opens_credentials_or_allows_tts(self):
        with patch('product_video.onboarding.setup', side_effect=AssertionError('No credentials')), \
             patch('product_video.cli.build') as build:
            execute(parser().parse_args(['auto', str(self.path)]))
        self.assertFalse(build.call_args.kwargs['allow_api'])

    def test_voice_and_voice_override_do_not_enable_tts(self):
        with patch('product_video.cli.generate', side_effect=AssertionError('No TTS')):
            for argv in (['voice', str(self.path)], ['build', str(self.path), '--voice', '小何 2.0']):
                with self.assertRaisesRegex(VideoError, '无配音'):
                    execute(parser().parse_args(argv))

    def test_duration_and_caption_validation_precedes_render(self):
        for value in (None, 0, -1, True, float('nan')):
            raw = copy.deepcopy(self.raw)
            raw['chapters'][0]['duration'] = value
            write_json(self.path, raw)
            with self.assertRaises(VideoError):
                load(self.path)
        raw = copy.deepcopy(self.raw)
        raw['chapters'][0]['captions'][-1]['end'] = 5
        write_json(self.path, raw)
        with self.assertRaisesRegex(VideoError, '不能超过'):
            load(self.path)

    def test_disabled_subtitles_and_legacy_audio_are_silent(self):
        config = load(self.path)
        config['video']['subtitles'] = 'none'
        chapters = prepare(config, allow_api=False)
        self.assertTrue(all(not c['cues'] for c in chapters))
        import wave
        with wave.open(str(master_audio(chapters, self.root)), 'rb') as sound:
            self.assertEqual(sound.getnframes() / sound.getframerate(), 6)
            self.assertFalse(any(sound.readframes(sound.getnframes())))

    def test_text_timing_preserves_unicode_and_non_overlapping_cues(self):
        value = '在这里查看案件。使用 Test 项目继续工作！'
        cues = from_text(value, 8, max_chars=10)
        self.assertEqual(''.join(c['text'] for c in cues).replace(' ', ''), value.replace(' ', ''))
        self.assertEqual(cues[0]['start'], 0)
        self.assertEqual(cues[-1]['end'], 8)
        self.assertTrue(all(a['end'] <= b['start'] for a, b in zip(cues, cues[1:])))


class PortabilityTests(unittest.TestCase):
    def test_default_mode_does_not_invalidate_old_tts_cache(self):
        chapter = {'narration': '已确认文稿'}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(cache_location(root, chapter, DEFAULT_VOICE),
                             cache_location(root, chapter, DEFAULT_VOICE | {'mode': 'tts'}))

    def test_utf8_atomic_output_and_cross_process_lock(self):
        with tempfile.TemporaryDirectory(prefix='锁 空格-') as temp:
            root = Path(temp)
            atomic_write(root / '文稿.txt', '案件材料'.encode('utf-8'))
            self.assertEqual((root / '文稿.txt').read_text(encoding='utf-8'), '案件材料')
            child = ('from pathlib import Path; import sys; from product_video.pipeline import project_lock; '
                     'lock=project_lock(Path(sys.argv[1])); lock.__enter__(); lock.__exit__(None,None,None)')
            with project_lock(root):
                process = subprocess.run([sys.executable, '-X', 'utf8', '-c', child, str(root)], capture_output=True, timeout=15)
                self.assertNotEqual(process.returncode, 0)
                self.assertIn('已有生成任务', process.stderr.decode('utf-8'))
            process = subprocess.run([sys.executable, '-X', 'utf8', '-c', child, str(root)], capture_output=True, timeout=15)
            self.assertEqual(process.returncode, 0, process.stderr.decode('utf-8'))

    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI')
    def test_dpapi_synthetic_store_roundtrip_does_not_save_plaintext(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'credentials.json'
            credentials.save_key('synthetic-test-only', path)
            self.assertNotIn('synthetic-test-only', path.read_text(encoding='utf-8'))
            self.assertEqual(credentials.load_key(path), 'synthetic-test-only')
            write_json(path, {'api_key': 'synthetic-test-only'})
            with self.assertRaises(VideoError):
                credentials.load_key(path)

    def test_windows_plan_requires_bound_window_and_read_only_actions(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'capture.json'
            raw = {'schema_version': 1, 'target': {'provider': 'windows', 'process_id': 123,
                'window_title': 'Synthetic Test'}, 'shots': [{'id': 'main', 'ready': {'role': 'window', 'name': 'Synthetic Test'}}]}
            write_json(path, raw)
            self.assertEqual(read_plan(path)['target']['process_id'], 123)
            raw['shots'][0]['actions'] = [{'action': 'press', 'target': {'role': 'button', 'name': 'Delete'}}]
            write_json(path, raw)
            with self.assertRaises(VideoError):
                read_plan(path)
            with self.assertRaises(VideoError):
                _validated_target({'provider': 'windows', 'window_title': 'Unbound'})

    def test_windows_ambiguous_and_minimized_windows_are_rejected(self):
        candidate = {'process_id': 123, 'window_handle': 456, 'window_title': 'Synthetic Test',
                     'visible': True, 'minimized': False, 'width': 800, 'height': 600}
        with self.assertRaises(VideoError):
            _choose_window({'process_id': 123}, [candidate, candidate | {'window_handle': 789}])
        with self.assertRaises(VideoError):
            _choose_window({'process_id': 123}, [candidate | {'minimized': True}])


if __name__ == '__main__':
    unittest.main()
