from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import Mock, patch
from leo.config import Settings
from leo.voice import Voice


class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(Path(self.temp.name) / "settings.json")
        self.model = Path(self.temp.name) / "model.bin"
        self.model.write_bytes(b"test placeholder, never run inference")
        self.voice = Voice(self.settings)
        self.addCleanup(self.voice.close)

    def test_no_microphone_process_without_permission_or_model(self):
        with patch("leo.voice.subprocess.Popen") as popen:
            with self.assertRaises(PermissionError): self.voice.start("owner")
            self.settings.update({"microphone": True})
            with self.assertRaises(RuntimeError): self.voice.start("owner")
            popen.assert_not_called()

    def test_recording_is_bounded_and_owner_disconnect_removes_audio(self):
        self.settings.update({"microphone": True, "whisper_model": str(self.model)})
        process = Mock()
        process.poll.return_value = None
        with patch("leo.voice.shutil.which", side_effect=lambda name: "/usr/bin/" + name), \
                patch("leo.voice.subprocess.Popen", return_value=process), patch("leo.voice.threading.Timer") as timer:
            self.voice.start("owner")
            directory = Path(self.voice.directory.name)
            self.assertEqual(timer.call_args.args[0], 20)
            with self.assertRaises(PermissionError): self.voice.stop("other")
            self.voice.disconnect("owner")
            process.send_signal.assert_called_with(signal.SIGINT)
            self.assertFalse(directory.exists())
            self.assertIsNone(self.voice.process)

    def test_transcription_failure_always_removes_audio(self):
        self.settings.update({"microphone": True, "whisper_model": str(self.model)})
        process = Mock()
        process.poll.return_value = 0
        with patch("leo.voice.shutil.which", side_effect=lambda name: "/usr/bin/" + name), \
                patch("leo.voice.subprocess.Popen", return_value=process), patch("leo.voice.threading.Timer"), \
                patch("leo.voice.subprocess.run", side_effect=RuntimeError("whisper failure")):
            self.voice.start("owner")
            directory = Path(self.voice.directory.name)
            with self.assertRaises(RuntimeError): self.voice.stop("owner")
            self.assertFalse(directory.exists())
            self.assertIsNone(self.voice.process)


if __name__ == "__main__":
    unittest.main()
