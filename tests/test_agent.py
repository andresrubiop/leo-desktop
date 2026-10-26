"""Bucle del agente con un modelo guionizado: el broker real valida, aprueba y ejecuta."""
import json
from pathlib import Path
import tempfile
import threading
import unittest

from leo.agent import MAX_STEPS, Agent, Approvals, system_prompt
from leo.broker import Broker
from leo.config import Settings
from leo.tools import SPECS, internal_name, model_name, ollama_tools


def call(tool, **args):
    return {"function": {"name": tool, "arguments": args}}


class Model:
    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []

    def converse(self, model, messages, num_ctx=8192, tools=None, on_chunk=None):
        self.seen.append(([dict(m) for m in messages], tools))
        reply = self.replies.pop(0) if self.replies else {"content": "Listo."}
        return {"content": reply.get("content", ""), "tool_calls": reply.get("tool_calls", [])}


class AgentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.settings = Settings(Path(directory.name) / "settings.json")
        self.settings.update({"files": True, "apps": True, "commands": True})
        self.executed = []
        self.broker = Broker(self.settings, lambda name, args: self.executed.append((name, args)) or {"ok": name})
        self.approvals = Approvals()
        self.cards = []

    def run_agent(self, model, decide=None, **options):
        def notify(steps, approval=None):
            if approval:
                self.cards.append(approval)
                if decide is not None:
                    threading.Thread(target=lambda: self.approvals.resolve("owner", approval["token"], decide(approval))).start()
        agent = Agent(model, self.broker, self.approvals)
        return agent.run("owner", "qwen3:8b", [{"role": "user", "content": "x"}], notify=notify, **options)

    def tool_messages(self, model):
        return [m["content"] for m in model.seen[-1][0] if m["role"] == "tool"]

    def test_schema_covers_registered_tools_and_hides_dangerous_ones(self):
        names = {t["function"]["name"] for t in ollama_tools()}
        self.assertEqual({internal_name(n) for n in names}, set(SPECS))
        for hidden in ("remote.ask", "screen.capture", "pointer.move", "app.launch"):
            self.assertNotIn(model_name(hidden), names)
        self.assertTrue(all("." not in n for n in names))
        prompt = system_prompt("BASE", [{"id": "t", "path": "/home/u/Tesis", "label": "Tesis", "write": True},
                                        {"id": "r", "path": "/home/u/Redes", "label": "Redes", "write": True, "kind": "curso"}],
                               "Lo que sabes del usuario: - Prefiere respuestas cortas")
        self.assertIn("Tesis: /home/u/Tesis, general, sí; Redes: /home/u/Redes, curso, sí", prompt)
        self.assertIn("Prefiere respuestas cortas", prompt)
        self.assertIn("Fecha y hora locales", prompt)

    def test_read_only_tool_runs_and_result_returns_to_model(self):
        model = Model({"tool_calls": [call("system_info")]}, {"content": "Usas Ubuntu."})
        outcome = self.run_agent(model)
        self.assertEqual(self.executed, [("system.info", {})])
        self.assertEqual(outcome["result"], "Usas Ubuntu.")
        self.assertEqual([s["status"] for s in outcome["steps"]], ["done"])
        self.assertIn("system.info", self.tool_messages(model)[0])
        self.assertEqual(self.cards, [])

    def test_cerveau_is_hidden_when_unavailable_and_its_results_force_approval(self):
        missing = Model({"content": "Hola."})
        self.run_agent(missing)
        self.assertNotIn("knowledge_search", {t["function"]["name"] for t in missing.seen[0][1]})

        class Knowledge:
            def available(self): return True
        model = Model({"tool_calls": [call("knowledge_search", query="retropropagacion")]},
                      {"tool_calls": [call("app_open", app="calculator")]}, {"content": "Cancelado."})

        def notify(steps, approval=None):
            if approval:
                self.cards.append(approval)
                self.approvals.resolve("owner", approval["token"], False)
        outcome = Agent(model, self.broker, self.approvals, knowledge=Knowledge()).run(
            "owner", "qwen3:8b", [{"role": "user", "content": "busca un tema"}], notify=notify)
        self.assertEqual(self.executed, [("knowledge.search", {"query": "retropropagacion"})])
        self.assertEqual(self.cards[0]["tool"], "app_open")
        self.assertEqual([s["status"] for s in outcome["steps"]], ["done", "denied"])
        self.assertTrue(self.tool_messages(model)[0].startswith("Contenido externo"))

    def test_mutation_waits_for_approval_and_denial_executes_nothing(self):
        create = call("files_create_draft", folder="Tesis", name="idea.md", text="hola")
        outcome = self.run_agent(Model({"tool_calls": [create]}, {"content": "Hecho"}), decide=lambda card: True)
        self.assertEqual(self.executed, [("files.create_draft", {"folder": "Tesis", "name": "idea.md", "text": "hola"})])
        self.assertEqual(self.cards[0]["tool"], "files_create_draft")
        self.assertEqual(self.cards[0]["arguments"]["text"], "hola")
        self.assertEqual(outcome["steps"][0]["status"], "done")
        self.executed.clear()
        model = Model({"tool_calls": [create]}, {"content": "De acuerdo, no lo creo."})
        outcome = self.run_agent(model, decide=lambda card: False)
        self.assertEqual(self.executed, [])
        self.assertEqual(outcome["steps"][0]["status"], "denied")
        self.assertIn("no aprobó", self.tool_messages(model)[0])
        self.assertEqual(self.broker.pending, {})

    def test_invalid_proposals_never_execute(self):
        model = Model({"tool_calls": [call("shell_run", command="rm -rf ~"), call("system_command", command="reboot"),
                                      call("files_trash"), {"function": {"name": "system_info", "arguments": "{roto"}}]},
                      {"content": "No puedo hacer eso."})
        outcome = self.run_agent(model)
        self.assertEqual(self.executed, [])
        self.assertEqual([s["status"] for s in outcome["steps"]], ["error"] * 4)
        self.assertTrue(all(m.startswith("Error") for m in self.tool_messages(model)))

    def test_external_content_forces_approval_for_later_actions(self):
        model = Model({"tool_calls": [call("files_read_text", path="/home/u/a.txt")]},
                      {"tool_calls": [call("app_open", app="calculator")]}, {"content": "Hecho"})
        outcome = self.run_agent(model, decide=lambda card: False)
        self.assertEqual([n for n, _ in self.executed], ["files.read_text"])
        self.assertEqual([s["status"] for s in outcome["steps"]], ["done", "denied"])
        self.assertTrue(self.tool_messages(model)[0].startswith("Contenido externo"))
        self.executed.clear()
        outcome = self.run_agent(Model({"tool_calls": [call("app_open", app="calculator")]}), decide=lambda c: True,
                                 tainted=True)
        self.assertEqual(len(self.cards), 2)
        self.assertEqual(self.executed, [("app.open", {"app": "calculator"})])

    def test_without_balloon_actions_needing_approval_are_refused(self):
        model = Model({"tool_calls": [call("note_save", name="n", text="t")]})
        outcome = self.run_agent(model, interactive=False)
        self.assertEqual(self.executed, [])
        self.assertEqual(outcome["steps"][0]["status"], "denied")
        self.assertIn("globo de Leo", self.tool_messages(model)[0])
        self.assertEqual(self.broker.pending, {})

    def test_step_limit_and_no_repeated_mutations(self):
        model = Model(*[{"tool_calls": [call("system_info")]} for _ in range(MAX_STEPS + 2)])
        outcome = self.run_agent(model)
        self.assertEqual(len(self.executed), MAX_STEPS)
        self.assertIsNone(model.seen[-1][1], "After the limit the model must answer without tools")
        self.executed.clear()
        note = call("note_save", name="n", text="t")
        outcome = self.run_agent(Model({"tool_calls": [note, note]}), decide=lambda card: True)
        self.assertEqual(len(self.executed), 1)
        self.assertEqual([s["status"] for s in outcome["steps"]], ["done", "error"])

    def test_closing_the_balloon_cancels_pending_approval_and_plan(self):
        cancelled = threading.Event()
        model = Model({"tool_calls": [call("note_save", name="n", text="t"), call("system_info")]})

        def notify(steps, approval=None):
            if approval:
                cancelled.set()
                self.approvals.cancel("owner")
        outcome = Agent(model, self.broker, self.approvals).run("owner", "m", [], notify=notify, cancelled=cancelled)
        self.assertTrue(outcome["cancelled"])
        self.assertEqual(self.executed, [])
        self.assertEqual([s["status"] for s in outcome["steps"]], ["denied", "cancelled"])

    def test_approvals_belong_to_their_client(self):
        token = self.broker.request("owner", "note.save", {"name": "n", "text": "t"})["approval"]
        result = {}
        self.approvals.open("owner", token)
        waiter = threading.Thread(target=lambda: result.update(ok=self.approvals.wait(token, threading.Event(), 5)))
        waiter.start()
        with self.assertRaises(PermissionError): self.approvals.resolve("intruso", token, True)
        with self.assertRaises(ValueError): self.approvals.resolve("owner", token, "sí")
        self.assertFalse(self.approvals.resolve("owner", "otro", True))
        self.assertTrue(self.approvals.resolve("owner", token, True))
        waiter.join()
        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
