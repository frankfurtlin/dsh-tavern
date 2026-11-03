"""Lossless request-local references and rows for mandatory fact context.

Only declared identifier fields are encoded. Prose, evidence quotations, scope,
voice and dialogue remain unchanged. Unknown shapes use the original format.
"""
from copy import deepcopy
import json

FACT_COLUMNS = ('id', 'subjectId', 'subject', 'predicate', 'value', 'evidenceIds',
                'knownBy', 'occurredOrder', 'temporalRelation', 'readerRevealAnchor', 'knowledgeAt')
KNOWLEDGE_COLUMNS = ('characterId', 'evidenceIds', 'learnedAt', 'learnedOrder', 'validity')
HEADERS = ('contextEncoding', 'factColumns', 'knowledgeColumns', 'characterRefs', 'evidenceRefs', 'positionRefs')
ENCODING = 'context-rows-v2'
LEGACY_ENCODING = 'context-rows-v1'


def wire_json(data):
    return json.dumps(encode_context(data), ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def encode_context(data):
    original = deepcopy(data)
    keys = [key for key in ('confirmedFacts', 'characterFacts', 'facts') if key in original]
    if not keys or any(key in original for key in HEADERS) or not isinstance(original.get('sourcePositions'), dict):
        return original
    # Evidence quotes stay literal; only a declared ID can use the same table as
    # fact evidenceIds. A future evidence field must survive unchanged.
    if 'evidence' in original and (not isinstance(original['evidence'], list) or any(
            not isinstance(e, dict) or set(e) not in ({'id'}, {'id', 'quote'})
            or not isinstance(e['id'], str) or ('quote' in e and not isinstance(e['quote'], str))
            for e in original['evidence'])):
        return original
    # A new field or missing field must never disappear through a fixed-column DTO.
    for key in keys:
        if not isinstance(original[key], list):
            return original
        for fact in original[key]:
            if not isinstance(fact, dict) or set(fact) != set(FACT_COLUMNS) or not isinstance(fact['knowledgeAt'], list):
                return original
            if any(not isinstance(k, dict) or set(k) != set(KNOWLEDGE_COLUMNS) for k in fact['knowledgeAt']):
                return original
    tables = {'characterRefs': {}, 'evidenceRefs': {}, 'positionRefs': {}}
    inverse = {key: {} for key in tables}

    def reference(value, table, prefix):
        if not isinstance(value, str):
            raise ValueError('Identifier must be a string')
        if table == 'positionRefs' and value not in original['sourcePositions']:
            raise ValueError('Position reference is missing')
        if value not in inverse[table]:
            ref = prefix + str(len(tables[table]))
            inverse[table][value] = ref
            tables[table][ref] = value
        return inverse[table][value]

    def references(values, table, prefix):
        if not isinstance(values, list):
            raise ValueError('References must be a list')
        return [reference(value, table, prefix) for value in values]

    packed = deepcopy(original)
    try:
        for key in keys:
            rows = []
            for fact in original[key]:
                row = deepcopy(fact)
                row['subjectId'] = reference(row['subjectId'], 'characterRefs', 'c')
                row['knownBy'] = references(row['knownBy'], 'characterRefs', 'c')
                row['evidenceIds'] = references(row['evidenceIds'], 'evidenceRefs', 'e')
                row['readerRevealAnchor'] = reference(row['readerRevealAnchor'], 'positionRefs', 'p')
                knowledge = []
                for claim in row['knowledgeAt']:
                    claim['characterId'] = reference(claim['characterId'], 'characterRefs', 'c')
                    claim['evidenceIds'] = references(claim['evidenceIds'], 'evidenceRefs', 'e')
                    claim['learnedAt'] = reference(claim['learnedAt'], 'positionRefs', 'p')
                    knowledge.append([claim[column] for column in KNOWLEDGE_COLUMNS])
                row['knowledgeAt'] = knowledge
                rows.append([row[column] for column in FACT_COLUMNS])
            packed[key] = rows
        for evidence in packed.get('evidence', []):
            evidence['id'] = reference(evidence['id'], 'evidenceRefs', 'e')
    except ValueError:
        return original
    packed.update(contextEncoding=ENCODING, factColumns=list(FACT_COLUMNS), knowledgeColumns=list(KNOWLEDGE_COLUMNS), **tables)
    dumps = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return packed if len(dumps(packed)) < len(dumps(original)) else original


def decode_context(data):
    """Strict local verification of the wire format; never interprets prose as refs."""
    encoding = data.get('contextEncoding')
    if encoding not in (ENCODING, LEGACY_ENCODING):
        return deepcopy(data)
    if data.get('factColumns') != list(FACT_COLUMNS) or data.get('knowledgeColumns') != list(KNOWLEDGE_COLUMNS):
        raise ValueError('Invalid context columns')
    for table, prefix in (('characterRefs', 'c'), ('evidenceRefs', 'e'), ('positionRefs', 'p')):
        entries = data.get(table)
        if not isinstance(entries, dict) or set(entries) != {prefix + str(i) for i in range(len(entries))} or any(not isinstance(v, str) for v in entries.values()) or len(set(entries.values())) != len(entries):
            raise ValueError('Invalid reference table')
    if not isinstance(data.get('sourcePositions'), dict) or any(v not in data['sourcePositions'] for v in data['positionRefs'].values()):
        raise ValueError('Missing source position')

    def reference(value, table):
        if not isinstance(value, str) or value not in data[table]:
            raise ValueError('Invalid typed reference')
        return data[table][value]

    def references(values, table):
        if not isinstance(values, list):
            raise ValueError('References must be a list')
        return [reference(value, table) for value in values]

    def record(row, columns):
        if not isinstance(row, list) or len(row) != len(columns):
            raise ValueError('Invalid context row')
        return dict(zip(columns, deepcopy(row)))

    original = deepcopy(data)
    keys = [key for key in ('confirmedFacts', 'characterFacts', 'facts') if key in data]
    if not keys:
        raise ValueError('Missing fact rows')
    for key in keys:
        if not isinstance(data[key], list):
            raise ValueError('Invalid fact rows')
        facts = []
        for row in data[key]:
            fact = record(row, FACT_COLUMNS)
            fact['subjectId'] = reference(fact['subjectId'], 'characterRefs')
            fact['knownBy'] = references(fact['knownBy'], 'characterRefs')
            fact['evidenceIds'] = references(fact['evidenceIds'], 'evidenceRefs')
            fact['readerRevealAnchor'] = reference(fact['readerRevealAnchor'], 'positionRefs')
            if not isinstance(fact['knowledgeAt'], list):
                raise ValueError('Invalid knowledge rows')
            knowledge = []
            for claim_row in fact['knowledgeAt']:
                claim = record(claim_row, KNOWLEDGE_COLUMNS)
                claim['characterId'] = reference(claim['characterId'], 'characterRefs')
                claim['evidenceIds'] = references(claim['evidenceIds'], 'evidenceRefs')
                claim['learnedAt'] = reference(claim['learnedAt'], 'positionRefs')
                knowledge.append(claim)
            fact['knowledgeAt'] = knowledge
            facts.append(fact)
        original[key] = facts
    if encoding == ENCODING and 'evidence' in original:
        if not isinstance(original['evidence'], list):
            raise ValueError('Invalid evidence entries')
        for evidence in original['evidence']:
            if (not isinstance(evidence, dict) or set(evidence) not in ({'id'}, {'id', 'quote'})
                    or ('quote' in evidence and not isinstance(evidence['quote'], str))):
                raise ValueError('Invalid evidence entry')
            evidence['id'] = reference(evidence['id'], 'evidenceRefs')
    for key in HEADERS:
        original.pop(key)
    return original
