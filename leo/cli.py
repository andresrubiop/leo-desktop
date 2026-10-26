import argparse
import json
import os
import sys
from .ipc import Client


def agent_event(source, payload):
    """Hook entry point: always exits 0 quickly and prints nothing, so it never blocks the agent."""
    try:
        from .agents import parse_claude, parse_codex
        raw = sys.stdin.buffer.read(1_000_001) if source == "claude" else (payload or "").encode()
        event = (parse_claude if source == "claude" else parse_codex)(raw)
        if event:
            Client().call({"op": "agent", **event}, timeout=3000)
    except Exception:
        pass
    raise SystemExit(0)


def usage(client, width, watch, plain):
    """The AI Usage Widget's view in this terminal (F7.3): once, or live every 2 s with --watch (Ctrl+C exits)."""
    import shutil
    import time
    from .widget.collect import ansi
    from .widget.render import Line, identity, prompt_segments, to_ansi
    width = width or max(40, min(120, shutil.get_terminal_size((80, 24)).columns - 1))
    color = not plain and sys.stdout.isatty()
    first = True
    while True:
        result = client.call({"op": "ai", "action": "snapshot", "args": {"width": width, "refresh": first}}, timeout=15000)
        for _ in range(16 if first else 0):  # The first reading runs in the background: give it a few seconds.
            if not result.get("ok") or not result.get("refreshing"):
                break
            time.sleep(.5)
            result = client.call({"op": "ai", "action": "snapshot", "args": {"width": width}}, timeout=15000)
        if not result.get("ok"):
            print(result.get("error", "Leo no responde"), file=sys.stderr)
            raise SystemExit(1)
        top = Line()
        for text, style in prompt_segments(*identity(), "leoctl usage"):
            top.add(text, style)
        status = Line()
        for text, style in result["status"]:
            status.add(text, style)
        if not result.get("live"):
            status.add(" · cuotas en vivo apagadas", "yellow")
        body = "\n".join((to_ansi([top], color), ansi(result["lines"]) if color else "\n".join(
            "".join(text for text, _ in line).rstrip() for line in result["lines"]), to_ansi([status], color)))
        if watch:
            sys.stdout.write("\x1b[H\x1b[2J" if color else "\n")
        print(body, flush=True)
        if not watch:
            raise SystemExit(0)
        first = False
        try:
            time.sleep(2)
        except KeyboardInterrupt:
            raise SystemExit(0) from None


def main():
    parser = argparse.ArgumentParser(description="LEO: estado, modelos y acciones con permisos")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "models"):
        commands.add_parser(name)
    commands.add_parser("chat").add_argument("prompt")
    knowledge = commands.add_parser("knowledge", help="Estado de Cerveau e indexación de cursos/investigación autorizados")
    knowledge.add_argument("action", choices=("status", "index"))
    setting = commands.add_parser("settings")
    setting.add_argument("json", help='Ejemplo: {"motion":false}')
    tool = commands.add_parser("tool")
    tool.add_argument("name")
    tool.add_argument("json", nargs="?", default="{}")
    notify = commands.add_parser("notify", help='Aviso a Leo, p. ej.: python train.py; leoctl notify --event done --title "train"')
    notify.add_argument("--event", choices=("done", "failed", "info"), required=True)
    notify.add_argument("--title", required=True)
    notify.add_argument("--project", default="")
    agent = commands.add_parser("agent-event", help="Hook de Claude Code (stdin) o Codex (último argumento)")
    agent.add_argument("--source", choices=("claude", "codex"), required=True)
    agent.add_argument("payload", nargs="?", default="")
    view = commands.add_parser("usage", help="Uso de IA, CPU/RAM/GPU, Ollama y ComfyUI, como el AI Usage Widget")
    view.add_argument("--watch", action="store_true", help="Actualizar cada 2 s (Ctrl+C para salir)")
    view.add_argument("--width", type=int, default=0, help="Columnas (40–120); por defecto, el ancho de la terminal")
    view.add_argument("--plain", action="store_true", help="Sin colores")
    watch = commands.add_parser("watch", help="Vigilar un proceso y/o su log (capacidad «jobs»)")
    watch.add_argument("--pid", type=int)
    watch.add_argument("--log")
    args = parser.parse_args()
    if args.command == "agent-event":
        agent_event(args.source, args.payload)
    if args.command == "usage":
        try:
            usage(Client(), args.width, args.watch, args.plain)
        except KeyboardInterrupt:
            raise SystemExit(0) from None
    request = {"op": args.command}
    if args.command == "chat":
        request["prompt"] = args.prompt
    elif args.command == "knowledge":
        request.update(action=args.action, args={})
    elif args.command == "settings":
        request["changes"] = json.loads(args.json)
    elif args.command == "tool":
        request.update(name=args.name, args=json.loads(args.json))
    elif args.command == "notify":
        request.update(event=args.event, title=args.title, **({"project": args.project} if args.project else {}))
    elif args.command == "watch":
        if args.pid is None and not args.log:
            parser.error("watch requiere --pid y/o --log")
        request = {"op": "tool", "name": "job.watch", "args": {
            **({"pid": args.pid} if args.pid is not None else {}), **({"log": os.path.abspath(args.log)} if args.log else {})}}
    client = Client()
    result = client.call(request)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("approval"):
        approved = input("¿Autorizar exactamente esta acción? Escribe SI: ") == "SI"
        result = client.call({"op": "decide", "token": result["approval"], "approved": approved})
        print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
