"""F6.7: guías paso a paso; el modelo elige entre elementos reales y Leo comprueba cada cambio antes de seguir."""
import json
import unittest

from leo.guide import WAIT, Guide, goal, pick, wants_stop


class Wizard:
    """A fake three-step window: Siguiente → Acepto las condiciones → Finalizar → «Configuración terminada»."""

    def __init__(self):
        self.page, self.accepted = 1, False

    def read(self):
        if self.page == 1:
            elements = [("push button", "Siguiente", ["enabled"])]
        elif self.page == 2:
            elements = [("check box", "Acepto las condiciones", ["enabled"] + (["checked"] if self.accepted else [])),
                        ("push button", "Finalizar", ["enabled"] if self.accepted else [])]
        else:
            elements = [("label", "Configuración terminada", [])]
        return {"window": "Asistente de prueba", "elements": [
            {"n": i, "role": r, "name": n, "states": s, "actions": []} for i, (r, n, s) in enumerate(elements)]}

    def act(self, name):
        if name == "Siguiente":
            self.page = 2
        elif name == "Acepto las condiciones":
            self.accepted = True
        elif name == "Finalizar" and self.accepted:
            self.page = 3


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class GuideTests(unittest.TestCase):
    def model(self, prompt):
        """A stand-in for the local model: the next element of the wizard, or done when the window says so."""
        if "Configuración terminada" in prompt:
            return '{"done": true, "say": "Configuración terminada"}'
        for name, say in (("Siguiente", "Pulsa Siguiente"), ("Acepto las condiciones", "Marca Acepto"), ("Finalizar", "Pulsa Finalizar")):
            usable = prompt.split("Elementos que se pueden usar ahora:")[1].split("Desactivados")[0]
            if f"«{name}»" in usable and f"({name})" not in prompt and "[marcada]" not in usable.split(f"«{name}»")[1][:30]:
                return json.dumps({"done": False, "element": name, "say": say})
        return "no sé"

    def guide(self, user_acts=True, chat=None):
        wizard, clock, pointed, finished = Wizard(), Clock(), [], []

        def point(name, say):
            pointed.append((name, say))
            if user_acts:
                wizard.act(name)  # The user does what Leo points at.
        guide = Guide("terminar el asistente", chat or self.model, wizard.read, point, finished.append,
                      clock=clock, sleep=clock.sleep)
        guide.run()
        return guide, pointed, finished, clock

    def test_three_steps_are_pointed_verified_and_finished(self):
        guide, pointed, finished, _ = self.guide()
        self.assertEqual([name for name, _ in pointed], ["Siguiente", "Acepto las condiciones", "Finalizar"])
        self.assertEqual([s["status"] for s in guide.steps], ["done", "done", "done"])
        self.assertEqual(finished, ["Listo: Configuración terminada"])

    def test_no_change_ends_the_guide_after_waiting(self):
        guide, pointed, finished, clock = self.guide(user_acts=False)
        self.assertEqual(len(pointed), 1)
        self.assertGreaterEqual(clock.now, WAIT)
        self.assertIn("no vi cambios", finished[0])
        self.assertEqual(guide.steps[0]["status"], "error")

    def test_a_model_that_names_nothing_real_stops_instead_of_guessing(self):
        _, pointed, finished, _ = self.guide(chat=lambda prompt: '{"done": false, "element": "Botón mágico"}')
        self.assertEqual(pointed, [])
        self.assertIn("No sé cuál es el siguiente paso", finished[0])

    def test_requests_and_stop_words(self):
        self.assertEqual(goal("Leo, guíame para cambiar la impresora predeterminada"), "cambiar la impresora predeterminada")
        self.assertEqual(goal("enséñame a exportar el PDF"), "exportar el PDF")
        self.assertEqual(goal("ayúdame paso a paso con la configuración del wifi"), "la configuración del wifi")
        self.assertIsNone(goal("¿qué ves?"))
        self.assertTrue(wants_stop("termina la guía"))
        self.assertTrue(wants_stop("Leo, para la guía por favor"))
        self.assertFalse(wants_stop("¿para qué sirve esto?"))
        elements = [{"name": "Acepto las condiciones"}, {"name": "Finalizar"}]
        self.assertEqual(pick("acepto las condiciones", elements), "Acepto las condiciones")
        self.assertEqual(pick("Finalizar", elements), "Finalizar")
        self.assertIsNone(pick("Cancelar", elements))
        elements = [{"name": "Siguiente", "role": "button"}]
        self.assertEqual(pick("button: Siguiente", elements), "Siguiente")  # Seen with qwen3:8b.
        self.assertEqual(pick("«Siguiente» (button)", elements), "Siguiente")
