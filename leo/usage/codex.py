"""Cliente del protocolo público Codex App Server, limitado a cuenta y cuotas.

No expone thread/turn, prompts, herramientas ni métodos de inferencia.
"""
import json
import os
import selectors
import shutil
import subprocess
import tempfile
import time


def read_account(path, timeout=25):
    executable = shutil.which("codex")
    if not executable:
        raise RuntimeError("Codex CLI no está instalado")
    env = {**os.environ, "CODEX_HOME": str(path)}
    with tempfile.TemporaryDirectory(prefix="leo-account-") as cwd:
        process = subprocess.Popen([executable, "app-server", "--listen", "stdio://"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   env=env, cwd=cwd)
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        buffer = b""
        deadline = time.monotonic() + timeout
        received = 0

        def send(message):
            process.stdin.write(json.dumps(message).encode() + b"\n")
            process.stdin.flush()

        def rpc(ident, method, params=None):
            nonlocal buffer, received
            if method not in {"initialize", "account/read", "account/rateLimits/read"}:
                raise ValueError("Método no permitido")
            send({"id": ident, "method": method, "params": params or {}})
            while time.monotonic() < deadline:
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    try:
                        response = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(response, dict) and response.get("id") == ident:
                        if "error" in response:
                            raise RuntimeError("Codex no pudo consultar la cuenta o sus cuotas. Revisa el inicio de sesión.")
                        return response.get("result") or {}
                if not selector.select(max(0, deadline - time.monotonic())):
                    break
                chunk = os.read(process.stdout.fileno(), 65536)
                received += len(chunk)
                if not chunk or received > 4 * 1024 * 1024:
                    break
                buffer += chunk
            raise RuntimeError("Codex no respondió a tiempo")

        try:
            rpc(1, "initialize", {"clientInfo": {"name": "leo_usage", "title": "Leo · uso de IA", "version": "0.5.0"}})
            send({"method": "initialized"})
            account = rpc(2, "account/read", {"refreshToken": False}).get("account")
            result = {"auth": "ok" if account else "nologin", "email": "", "plan": "", "windows": []}
            if not account:
                return result
            result.update(email=str(account.get("email") or ""), plan=str(account.get("planType") or account.get("type") or ""))
            if account.get("type") != "chatgpt":
                result["note"] = "Esta cuenta no publica cuotas de suscripción ChatGPT."
                return result
            try:
                limits = rpc(3, "account/rateLimits/read")
            except RuntimeError as exc:
                result["error"] = str(exc)
                return result
            buckets = limits.get("rateLimitsByLimitId") or {"codex": limits.get("rateLimits", {})}
            for key, bucket in buckets.items():
                for name in ("primary", "secondary"):
                    window = bucket.get(name)
                    if not window or window.get("usedPercent") is None:
                        continue
                    minutes = window.get("windowDurationMins") or 0
                    duration = f"{minutes // 1440}d" if minutes and minutes % 1440 == 0 else f"{minutes // 60}h" if minutes and minutes % 60 == 0 else f"{minutes}m"
                    result["windows"].append({"label": f"{bucket.get('limitName') or key} · {duration}",
                                              "used": window["usedPercent"], "resets_at": window.get("resetsAt")})
            return result
        finally:
            selector.close()
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            process.stdin.close()
            process.stdout.close()
