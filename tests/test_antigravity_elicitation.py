import json
import unittest

from bridge.clients.antigravity.cli_session import _normalize_waiting_step, _build_interaction_response


def request(schema):
    return _normalize_waiting_step('request', 'app', 'chat', 'trajectory', 0, {
        'requestedInteraction': {'elicitation': {'requestedSchemaJson': json.dumps(schema)}}})


class ElicitationTests(unittest.TestCase):
    def schema(self, spec, required=True):
        return {'type': 'object', 'properties': {'answer': spec}, 'required': ['answer'] if required else []}

    def respond(self, spec, values, required=True):
        _, meta = request(self.schema(spec, required))
        result = _build_interaction_response(meta, 'accept', {'answer': {'answers': values}})
        return json.loads(result['elicitation']['contentJson'])

    def test_array_options_and_types_are_preserved(self):
        spec = {'type': 'array', 'items': {'type': 'integer', 'enum': [1, 2]}, 'minItems': 1, 'maxItems': 2}
        public, _ = request(self.schema(spec))
        q = public['input']['questions'][0]
        self.assertEqual([o['label'] for o in q['options']], ['1', '2'])
        self.assertTrue(q['multiSelect'])
        self.assertIs(q['allowCustom'], False)
        self.assertEqual(self.respond(spec, ['1', '2']), {'answer': [1, 2]})

    def test_invalid_boolean_is_not_converted_to_false(self):
        for value in ['maybe', '2', 'anything']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.respond({'type': 'boolean'}, [value])
        self.assertEqual(self.respond({'type': 'boolean'}, ['否 (false)']), {'answer': False})

    def test_required_answers_cannot_be_missing_or_skipped(self):
        _, meta = request(self.schema({'type': 'string'}))
        for answers in [{}, {'answer': {'skipped': True}}, {'answer': {'answers': []}}]:
            with self.subTest(answers=answers), self.assertRaises(ValueError):
                _build_interaction_response(meta, 'accept', answers)
        _, meta = request(self.schema({'type': 'string'}, required=False))
        result = _build_interaction_response(meta, 'accept', {'answer': {'skipped': True}})
        self.assertEqual(json.loads(result['elicitation']['contentJson']), {})

    def test_values_must_match_enum_bounds_and_scalar_cardinality(self):
        cases = [({'type': 'string', 'enum': ['one']}, ['two']),
                 ({'type': 'integer', 'minimum': 2}, ['1']),
                 ({'type': 'number', 'maximum': 4}, ['5']),
                 ({'type': 'number'}, ['NaN']), ({'type': 'number'}, ['Infinity']),
                 ({'type': 'string'}, ['one', 'two']),
                 ({'type': 'string', 'minLength': 2}, ['a']),
                 ({'type': 'array', 'items': {'type': 'string', 'enum': ['a', 'b']}, 'maxItems': 1}, ['a', 'b'])]
        for spec, values in cases:
            with self.subTest(spec=spec, values=values), self.assertRaises(ValueError):
                self.respond(spec, values)

    def test_unsupported_schema_cannot_become_generic_approval(self):
        for spec in [{'type': 'object', 'properties': {'secret': {'type': 'string'}}},
                     {'type': 'string', 'pattern': '^a+$'}, {'type': 'array'}, {'type': ['string', 'null']}]:
            with self.subTest(spec=spec):
                public, meta = request(self.schema(spec))
                self.assertEqual([d['id'] for d in public['decisions']], ['decline'])
                with self.assertRaises(ValueError):
                    _build_interaction_response(meta, 'accept', {'answer': {'answers': ['value']}})
                self.assertEqual(_build_interaction_response(meta, 'decline'), {'elicitation': {'action': 'decline'}})

    def test_shared_question_values_are_validated_as_lists(self):
        with self.assertRaises(ValueError):
            self.respond({'type': 'string'}, 'not-a-list')

    def test_string_enum_values_keep_exact_whitespace(self):
        self.assertEqual(self.respond({'type': 'string', 'enum': [' padded ']}, [' padded ']), {'answer': ' padded '})
        self.assertEqual(self.respond({'type': 'array', 'items': {'type': 'string', 'enum': [' a ', 'b']}}, [' a ', 'b']), {'answer': [' a ', 'b']})

