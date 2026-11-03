"""Public projection vectors; only pure functions, no queries or providers."""
import json
from pathlib import Path
from app.domain import canonical, digest
from app.prose.accepted_projection import project_accepted_text
from tests.test_accepted_projection import BODIES

vectors = []
for index, body in enumerate(BODIES):
    for style in ['local-v1', 'server-v1']:
        projection = project_accepted_text(body, style)
        encoded = canonical(projection)
        vectors.append({'name': f'{style}-{index + 1}', 'text': body, 'style': style,
                        'projection': projection, 'canonical': encoded, 'sha256': digest(encoded)})
Path('/evidence/accepted-projection-vectors.json').write_text(json.dumps(vectors, ensure_ascii=False, indent=2) + '\n', 'utf-8')
print({'publicVectors': len(vectors), 'modelCalls': 0})
