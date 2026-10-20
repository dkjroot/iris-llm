"""Offline checks for generated cues and playback boundaries; no audio hardware."""

from array import array
import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import wave

from iris_sounds import generate_sounds


class SoundTests(unittest.TestCase):
    """Retain original assets and the nonblocking/recording timing contract."""

    def test_bundled_cues_match_generator_and_are_quiet_pcm(self) -> None:
        """Verify the checked-in recordings actually come from the documented recipe."""
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            for filename, values in generate_sounds.cues().items():
                with self.subTest(filename=filename):
                    path = Path(directory) / filename
                    generate_sounds.write_wav(path, values)
                    self.assertEqual(path.read_bytes(), (generate_sounds.OUTPUT / filename).read_bytes())
                    with wave.open(str(path), 'rb') as source:
                        self.assertEqual((source.getnchannels(), source.getsampwidth(), source.getframerate()), (1, 2, 24000))
                        samples = array('h', source.readframes(source.getnframes()))
                    self.assertEqual(samples[0], 0)
                    self.assertEqual(samples[-1], 0)
                    self.assertGreater(max(abs(value) for value in samples), 0)
                    self.assertLess(max(abs(value) for value in samples), 32767 * 0.2)
                    self.assertEqual(len(samples), len(values))
                    if filename == 'workinglongquiet.wav':
                        self.assertLess(max(abs(value) for value in samples), 32767 * 0.04)
                        self.assertEqual(len(samples), 86400)

    def test_playback_loads_from_module_and_keeps_listening_cues_blocking(self) -> None:
        """Keep working feedback async and finish short cues before capture."""
        device = Mock()
        with patch.dict(sys.modules, {'sounddevice': device}):
            sys.modules.pop('iris_sounds.beeps_and_bleeps', None)
            module = importlib.import_module('iris_sounds.beeps_and_bleeps')
            device.play.assert_not_called()
            previous = Path.cwd()
            try:
                os.chdir(Path(__file__).parent)
                sounds = module.Sounds({})
            finally:
                os.chdir(previous)
            for name, blocking in [('play_working_sound', False), ('play_chirp', True),
                                   ('play_bleep', True), ('play_long_bleep', True)]:
                device.reset_mock()
                getattr(sounds, name)()
                device.stop.assert_called_once_with()
                self.assertEqual(device.play.call_args.kwargs, {'blocking': blocking})
                self.assertEqual(device.play.call_args.args[1], 24000)
            with self.assertRaisesRegex(FileNotFoundError, 'generate_sounds.py'):
                module.load_cue('missing.wav')
            sys.modules.pop('iris_sounds.beeps_and_bleeps', None)

    def test_replacement_cue_rejects_an_unsupported_pcm_layout(self) -> None:
        """A stereo override must produce an actionable error, not garbled playback."""
        with patch.dict(sys.modules, {'sounddevice': Mock()}):
            module = importlib.import_module('iris_sounds.beeps_and_bleeps')
            source = Mock()
            source.__enter__ = Mock(return_value=source)
            source.__exit__ = Mock(return_value=False)
            source.getnchannels.return_value = 2
            with patch.object(module.wave, 'open', return_value=source):
                with self.assertRaisesRegex(ValueError, 'mono 16-bit PCM'):
                    module.load_cue('chirp.wav')
            sys.modules.pop('iris_sounds.beeps_and_bleeps', None)


if __name__ == '__main__':
    unittest.main()
