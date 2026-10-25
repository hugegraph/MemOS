"""Pure runtime inventory tests; no model calls, environment defaults or storage access."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace as Obj
import unittest

spec = importlib.util.spec_from_file_location('catalog_under_test', Path(__file__).with_name('models.py'))
models = importlib.util.module_from_spec(spec)
spec.loader.exec_module(models)


def instance(name):
    return Obj(config=Obj(model_name_or_path=name, api_key='NEVER_EXPOSE', api_base='https://user:password@private/?token=secret'))


class CatalogTests(unittest.TestCase):
    def components(self):
        return {'llm': instance('manager'), 'mem_reader': Obj(llm=instance('reader')),
                'embedder': instance('embed'), 'reranker': Obj(model='real-reranker'), 'chat_llms': None}

    def test_runtime_instances_only_and_whitelist(self):
        c = self.components()
        rows = models.catalog(c)['records']
        self.assertEqual({r['model'] for r in rows}, {'manager', 'reader', 'embed', 'real-reranker'})
        self.assertTrue(all(set(r) == {'role', 'model', 'source', 'spec', 'group'} for r in rows))
        self.assertNotIn('NEVER_EXPOSE', json.dumps(rows))
        self.assertNotIn('private', json.dumps(rows))
        c['mem_reader'].llm.config.model_name_or_path = 'changed'
        self.assertIn('changed', {r['model'] for r in models.catalog(c)['records']})

    def test_chat_registry_names_not_default_and_disabled_not_invented(self):
        c = self.components()
        self.assertFalse(any(r['group'] == 'chat' for r in models.catalog(c)['records']))
        c['chat_llms'] = {'served-model': instance('client-default')}
        self.assertEqual([r['model'] for r in models.catalog(c)['records'] if r['group'] == 'chat'], ['served-model'])

    def test_local_reranking_and_optional_absent_slots_are_not_fake_models(self):
        c = self.components(); c['reranker'] = Obj()
        self.assertEqual(len(models.catalog(c)['records']), 3)
        c['mem_reader'].document_parser_llm = instance('document')
        self.assertEqual(len(models.catalog(c)['records']), 4)

    def test_bad_runtime_and_unbounded_or_sensitive_identifier_fail_closed(self):
        for value in [None, {}, {'mem_reader': Obj()}]:
            with self.assertRaises(models.CatalogUnavailable): models.catalog(value)
        for name in ['', 'x' * 257, 'bad\nname', 'https://user:secret@host', 'model?token=secret']:
            c = self.components(); c['llm'] = instance(name)
            with self.assertRaises(models.CatalogUnavailable): models.catalog(c)
        c = self.components(); c['chat_llms'] = {str(i): instance('x') for i in range(65)}
        with self.assertRaises(models.CatalogUnavailable): models.catalog(c)


if __name__ == '__main__': unittest.main()
