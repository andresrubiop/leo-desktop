"""Grabación bajo pulsación explícita, acotada a 20 s, transcripción local."""
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import threading


class Voice:
    def __init__(self, settings):
        self.settings = settings
        self.lock = threading.RLock()
        self.process = self.directory = self.timer = self.owner = None

    def start(self, owner):
        with self.lock:
            config = self.settings.snapshot()
            if not config["microphone"]:
                raise PermissionError("Habilita el micrófono en Permisos")
            model = Path(config["whisper_model"])
            if not config["whisper_model"] or not model.is_file() or not shutil.which("whisper-cli"):
                raise RuntimeError("Configura un modelo local de whisper.cpp e instala whisper-cli para usar dictado")
            if self.process:
                raise RuntimeError("Ya hay una grabación activa")
            binary = shutil.which("pw-record")
            if not binary:
                raise RuntimeError("Falta pw-record (PipeWire)")
            self.directory = tempfile.TemporaryDirectory(prefix="leo-audio-")
            try:
                self.process = subprocess.Popen([binary, "--rate", "16000", "--channels", "1",
                                                 str(Path(self.directory.name) / "input.wav")],
                                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                stderr=subprocess.DEVNULL)
            except Exception:
                self.directory.cleanup()
                self.directory = None
                raise
            self.owner = owner
            self.timer = threading.Timer(20, self._halt)
            self.timer.daemon = True
            self.timer.start()
            return "Grabando mientras mantienes pulsado (máximo 20 s)"

    def _halt(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                self.process.send_signal(signal.SIGINT)
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()

    def stop(self, owner, transcribe=True):
        with self.lock:
            if not self.process:
                return ""
            if owner != self.owner:
                raise PermissionError("La grabación pertenece a otro cliente")
            self.timer.cancel()
            self._halt()
            directory = self.directory
            self.process = self.directory = self.timer = self.owner = None
        try:
            if not transcribe:
                return ""
            config = self.settings.snapshot()
            if not config["microphone"]:
                raise PermissionError("Permiso de micrófono revocado")
            base = Path(directory.name)
            subprocess.run(["whisper-cli", "-m", config["whisper_model"], "-f", str(base / "input.wav"),
                            "-l", "es", "-nt", "-otxt", "-of", str(base / "transcript")],
                           capture_output=True, timeout=120, check=True)
            return (base / "transcript.txt").read_text().strip()[:16000]
        finally:
            directory.cleanup()

    def disconnect(self, owner):
        if self.owner == owner:
            self.stop(owner, transcribe=False)

    def close(self):
        if self.owner:
            self.stop(self.owner, transcribe=False)

    def speak(self, text):
        if not self.settings.snapshot()["voice"]:
            raise PermissionError("La voz está desactivada")
        binary = shutil.which("espeak-ng")
        if not binary:
            raise RuntimeError("Instala espeak-ng para lectura local en voz alta")
        subprocess.run([binary, "-v", "es", "--stdin"], input=text[:4000], text=True,
                       capture_output=True, timeout=60, check=True)
        return "Lectura finalizada"
