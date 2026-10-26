import json
import time
import unittest
from unittest.mock import Mock

from leo.harness import Harness, account_facts, intent


class HarnessTests(unittest.TestCase):
    def account(self, **changes):
        return {"id": "codex:test", "provider": "codex", "name": "personal", "kind": "profile",
                "auth": "ok", "fetched_at": time.time(), "source": "cache", "windows": [
                    {"label": "7d", "used": 93, "resets_at": time.time() + 3600}], **changes}

    def test_fresh_percent_is_not_tokens_and_expired_is_not_refilled(self):
        now = time.time()
        data = account_facts(self.account(fetched_at=now - 1), now)
        self.assertEqual(data['windows'][0]['remaining_percent'], 7)
        self.assertIsNone(data['exact_tokens_remaining'])
        for changes in ({"fetched_at": now - 400}, {"error": "offline"},
                        {"windows": [{"label": "5h", "used": 93, "resets_at": now - 1}]}):
            data = account_facts(self.account(**changes), now)
            self.assertIsNone(data['windows'][0]['remaining_percent'])
            self.assertEqual(data['windows'][0]['observed_used_percent'], 93)

    def test_spanish_followups_and_provider_selection(self):
        previous = [{'role': 'user', 'content': '¿Cuántos tokens me quedan en Claude?'}]
        result = intent('¿Y cuándo se renueva?', previous)
        self.assertTrue(result['usage'])
        self.assertEqual(result['providers'], ['claude'])
        self.assertEqual(intent('¿Y en Codex?', previous)['providers'], ['codex'])
        self.assertTrue(intent('¿Cómo voy con la IA?')['usage'])
        self.assertTrue(intent('¿Qué skills tengo instaladas?')['skills'])
        self.assertFalse(intent('Cuéntame un chiste')['usage'])

    def test_a_provider_or_a_token_alone_is_not_a_usage_question(self):
        # F2 baseline 2026-10-25: each of these read every account (up to 30 s) before answering.
        for text in ('¿Eres ChatGPT?', '¿Qué es un token en los modelos de lenguaje?', 'Busca en Google recetas',
                     'Soy profesor de Redes Neuronales; tenlo en cuenta en adelante', 'Lo hice por mi cuenta'):
            self.assertFalse(intent(text)['usage'], text)
        for text in ('¿Me quedan tokens en Claude y Codex?', '¿Cómo voy de cuota en Codex?', '¿Cómo voy con Claude?',
                     '¿Qué cuentas tengo?', 'Cuánto uso tengo'):
            self.assertTrue(intent(text)['usage'], text)

    def test_only_bounded_read_tools_and_no_credentials_in_context(self):
        accounts = [self.account(id=f'codex:{i}', fetched_at=1, token='SECRET', path='/private', email='secret@example.com') for i in range(11)]
        accounts.append(self.account(provider='claude', id='hidden', hidden=True))
        hub = Mock()
        def dispatch(action, args):
            if action == 'accounts': return {'accounts': accounts}
            if action == 'refresh': return {'account': self.account(id=args['id'], source='live')}
            self.fail('Mutation or unexpected tool: ' + action)
        hub.dispatch.side_effect = dispatch
        context, trace = Harness(hub).context('Cuánto uso tengo')
        self.assertEqual(trace.count('usage.refresh:codex'), 3)  # At most three live readings per question.
        report = json.loads(context[context.index('{'):])['usage']
        self.assertEqual(len(report['accounts']), 8)
        self.assertEqual(report['omitted_accounts'], 3)
        for private in ('SECRET', '/private', 'secret@example.com', 'hidden'):
            self.assertNotIn(private, context)

    def test_claude_refreshes_live_and_api_keys_stay_locked(self):
        # G18 (2026-10-23): Claude's quotas are read live like the widget; API keys are only read from the Accounts UI.
        claude = self.account(provider='claude', id='claude:x', fetched_at=1)
        key = self.account(provider='deepseek', id='key:x', kind='key', fetched_at=1)
        hub = Mock()

        def dispatch(action, args):
            if action == 'accounts': return {'accounts': [claude, key]}
            if action == 'refresh' and args == {'id': 'claude:x'}: return {'account': {**claude, 'fetched_at': time.time()}}
            self.fail('Unexpected call: %s %s' % (action, args))
        hub.dispatch.side_effect = dispatch
        context, trace = Harness(hub).context('Dime mis saldos y cuotas')
        self.assertEqual(trace, ['usage.accounts', 'usage.refresh:claude'])
        self.assertIn('historical', context)  # The API key's old reading stays historical.

    def test_failure_still_provides_context_to_local_model(self):
        hub = Mock()
        hub.dispatch.side_effect = RuntimeError('private debug output')
        context, _ = Harness(hub).context('¿Tengo tokens?')
        self.assertIn('No se pudo leer', context)
        self.assertNotIn('private debug', context)

    def test_regular_chat_does_not_read_accounts(self):
        hub = Mock()
        self.assertEqual(Harness(hub).context('Hola Leo'), ('', []))
        hub.dispatch.assert_not_called()

    def test_spanish_document_query_finds_english_document_skills(self):
        hub = Mock()
        hub.dispatch.return_value = {'skills': [{'name': f'azure-{i}', 'description': 'Cloud resource management'} for i in range(20)] + [
            {'name': 'google-docs', 'description': 'Create and edit native Google Docs'},
            {'name': 'google-sheets', 'description': 'Analyze spreadsheets'}]}
        context, _ = Harness(hub).context('¿Qué skills tengo para trabajar con documentos?')
        shown = json.loads(context[context.index('{'):])['skills']['shown']
        self.assertEqual([s['name'] for s in shown[:2]], ['google-docs', 'google-sheets'])

    def test_skill_descriptions_are_bounded_and_not_executed(self):
        hub = Mock()
        hub.dispatch.return_value = {'skills': [{'name': f'skill{i}', 'description': 'x' * 500} for i in range(30)]}
        context, trace = Harness(hub).context('¿Qué skills tengo?')
        data = json.loads(context[context.index('{'):])['skills']
        self.assertEqual(data['total'], 30)
        self.assertEqual(len(data['shown']), 16)
        self.assertEqual(trace, ['skills.list'])
        hub.dispatch.assert_called_once_with('skills', {})


if __name__ == '__main__':
    unittest.main()
