"""Cliente del test Wayland aislado; el único contenido es una escena sintética con código 7391.

argv[1]: JSON del compositor de prueba con los rectángulos esperados de ventana y monitor y los
identificadores de la ventana de prueba; o "modal" para comprobar el rechazo con un diálogo del sistema.
"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from leo.ipc import Client


def look(client, scope, question="¿Qué ves? Responde en una frase."):
    return client.call({"op": "tool", "name": "screen.look", "args": {"question": question}, "scope": scope})


client = Client(private=True)
try:
    if sys.argv[1] == "modal":
        reply = look(client, "monitor")
        assert not reply["ok"] and "diálogo del sistema" in reply["error"], reply
        print("LEO_CAPTURE_MODAL_PASS: con un diálogo de contraseña o permisos abierto no se captura", flush=True)
        raise SystemExit(0)
    scene = json.loads(sys.argv[1])
    args = {"question": "¿Qué código de cuatro cifras aparece? Responde solo con las cifras."}
    denied = client.call({"op": "tool", "name": "screen.look", "args": args})
    assert not denied["ok"] and "screen" in denied["error"], denied
    assert client.call({"op": "settings", "changes": {"screen": True, "vision_model": "qwen2.5vl:7b"}})["ok"]
    reply = client.call({"op": "tool", "name": "screen.look", "args": args, "scope": "all"})
    assert reply["ok"] and "7391" in reply["result"]["answer"], reply
    assert reply["result"]["captured"]["scope"] == "all", reply
    print("LEO_NATIVE_CAPTURE_PASS: GNOME → PNG en memoria → modelo visual real → código 7391, sin selector ni archivos", flush=True)

    window = look(client, "window")
    assert window["ok"] and window["result"]["captured"]["scope"] == "window", window
    assert window["result"]["image"] == scene["window"][2:], (window["result"]["image"], scene["window"])
    monitor = look(client, "monitor")
    assert monitor["ok"] and monitor["result"]["captured"]["scope"] == "monitor", monitor
    assert monitor["result"]["image"] == scene["monitor"][2:], (monitor["result"]["image"], scene["monitor"])
    print(f"LEO_CAPTURE_SCOPE_PASS: ventana {scene['window'][2:]} y monitor {scene['monitor'][2:]} capturados a tamaño real", flush=True)

    defaults = client.call({"op": "status"})["settings"]["protected_apps"]
    assert client.call({"op": "settings", "changes": {"protected_apps": defaults + scene["ids"][:1]}})["ok"]
    refused = look(client, "window")
    assert not refused["ok"] and "protegida" in refused["error"], refused
    assert look(client, "monitor")["ok"]
    assert client.call({"op": "settings", "changes": {"protected_apps": defaults}})["ok"]
    print("LEO_CAPTURE_PROTECTED_PASS: ventana protegida rechazada en ámbito ventana y oculta al capturar el monitor", flush=True)

    located = client.call({"op": "tool", "name": "screen.locate", "args": {"target": "el texto CODIGO 7391"}, "scope": "all"})
    assert located["ok"] and located["result"]["located"], located
    print("LEO_LOCATE_SENT: cuadro localizado y enviado en coordenadas del escenario", flush=True)

    reply = client.call({"op": "chat", "prompt": "Leo, mira las dos pantallas y dime qué código de cuatro cifras aparece.",
                         "request_id": "capture-test"}, timeout=300000)
    assert reply["ok"] and "7391" in reply["result"], reply
    assert any(s["tool"] == "screen.look" and s["status"] == "done" for s in reply["steps"]), reply
    assert not any(s["status"] == "approval" for s in reply["steps"]), reply
    print("LEO_NATIVE_CHAT_PASS: petición en chat → captura nativa → visión real → respuesta local sin aprobación extra", flush=True)
finally:
    client.close()
