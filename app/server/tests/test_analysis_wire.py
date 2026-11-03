"""Compact provider JSON must preserve the complete internal evidence contract."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.schemas import AnalysisOutput


def test_compact_analysis_preserves_semantics_and_rejects_ambiguous_fields():
    compact = {
        'v': 1, 'h': 'full-scope-hash',
        'c': [{'i': 'c1', 'n': '顾舟', 'e': ['e1'], 'p': 'prior-identity', 'y': 'explicit'}],
        'f': [{'i': 'f1', 's': 'c1', 'p': 'action', 'v': '亲手放入木楔',
               'c': 'explicit', 'e': ['e1'], 'o': 'explicit-scene', 't': 'past',
               'k': ['c1'], 'l': [{'c': 'c1', 'e': ['e1'], 'o': 'explicit-learning', 'v': 'future'}]}],
        'e': [{'i': 'e1', 'b': 'complete-source-block-id', 'q': '顾舟亲手将木楔放入门框。'}],
        'u': ['时点尚不明确'], 's': '有证据的行动',
    }
    full = {
        'schemaVersion': 1, 'scopeHash': 'full-scope-hash',
        'characters': [{'localId': 'c1', 'displayName': '顾舟', 'evidenceIds': ['e1'],
                        'priorRef': 'prior-identity', 'continuity': 'explicit'}],
        'facts': [{'localId': 'f1', 'subjectRef': 'c1', 'predicate': 'action', 'value': '亲手放入木楔',
                   'certainty': 'explicit', 'evidenceIds': ['e1'], 'occurredOrder': 'explicit-scene',
                   'temporalRelation': 'past', 'knownBy': ['c1'],
                   'knowledge': [{'characterRef': 'c1', 'evidenceIds': ['e1'],
                                  'learnedOrder': 'explicit-learning', 'validity': 'future'}]}],
        'evidence': [{'id': 'e1', 'blockId': 'complete-source-block-id', 'quote': '顾舟亲手将木楔放入门框。'}],
        'unresolved': ['时点尚不明确'], 'summary': '有证据的行动',
    }
    assert AnalysisOutput.model_validate(compact).model_dump() == full
    assert AnalysisOutput.model_validate(full).model_dump() == full
    invalid = []
    duplicate = deepcopy(compact); duplicate['schemaVersion'] = 1; invalid.append(duplicate)
    duplicate = deepcopy(compact); duplicate['f'][0]['subjectRef'] = 'different'; invalid.append(duplicate)
    unknown = deepcopy(compact); unknown['f'][0]['l'][0]['unknown'] = True; invalid.append(unknown)
    enum = deepcopy(compact); enum['f'][0]['l'][0]['v'] = 'already_known'; invalid.append(enum)
    version = deepcopy(compact); version['v'] = '1'; invalid.append(version)
    missing = deepcopy(compact); del missing['f'][0]['s']; invalid.append(missing)
    numeric_order = deepcopy(compact); numeric_order['f'][0]['o'] = 1; invalid.append(numeric_order)
    numeric_learned_order = deepcopy(compact); numeric_learned_order['f'][0]['l'][0]['o'] = 1; invalid.append(numeric_learned_order)
    invalid_continuity = deepcopy(compact); invalid_continuity['c'][0]['y'] = 'new'; invalid.append(invalid_continuity)
    for value in invalid:
        with pytest.raises(ValidationError):
            AnalysisOutput.model_validate(value)
