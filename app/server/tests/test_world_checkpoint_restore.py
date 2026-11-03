"""Independent full-checkpoint contract; fresh PostgreSQL, synthetic owner, mock only."""
import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func

from app.config import settings
from app.db import SessionLocal, engine
from app.main import app
from app.models import Task, UsageRecord
from app.providers import MockAdapter, ChatCompletionsAdapter
from app.world.models import World, WorldLine, WorldCheckpoint, WorldMemory, WorldEvent
from app.world.state import checkpoint_hash
from app.world.memory import context
from app.world.worker import process

import os
ARTIFACT_DIR = os.environ.get("WORLD_RESTORE_ARTIFACT_DIR")
API = '/api/world/v1'
EXPECTED = json.loads(r'''{
  "before": {
    "definition": {
      "version": 3,
      "title": "港口合成世界",
      "description": "仅供此次恢复验证",
      "narrativeStyle": "合成简洁",
      "timeLabel": "午后",
      "location": "port",
      "entities": [
        {
          "id": "alice",
          "name": "爱丽丝",
          "type": "character",
          "aliases": [
            "小艾"
          ],
          "description": "爱丽丝的基础资料",
          "traits": "合成特征",
          "source": "user",
          "evidence": [
            {
              "blockId": "synthetic-block",
              "quote": "船灯"
            }
          ],
          "hiddenGoal": "",
          "knownFacts": [
            "letter-fact"
          ],
          "knownBy": [],
          "stackable": false
        },
        {
          "id": "npc",
          "name": "旅人",
          "type": "character",
          "aliases": [],
          "description": "旅人的基础资料",
          "traits": "合成特征",
          "source": "user",
          "evidence": [
            {
              "blockId": "synthetic-block",
              "quote": "船灯"
            }
          ],
          "hiddenGoal": "私下筹备远行",
          "knownFacts": [],
          "knownBy": [
            "alice"
          ],
          "stackable": false
        },
        {
          "id": "port",
          "name": "港口",
          "type": "place",
          "aliases": [],
          "description": "港口的基础资料",
          "traits": "合成特征",
          "source": "user",
          "evidence": [
            {
              "blockId": "synthetic-block",
              "quote": "船灯"
            }
          ],
          "hiddenGoal": "",
          "knownFacts": [],
          "knownBy": [],
          "stackable": false
        },
        {
          "id": "far",
          "name": "远城",
          "type": "place",
          "aliases": [],
          "description": "远城的基础资料",
          "traits": "合成特征",
          "source": "user",
          "evidence": [
            {
              "blockId": "synthetic-block",
              "quote": "船灯"
            }
          ],
          "hiddenGoal": "",
          "knownFacts": [],
          "knownBy": [],
          "stackable": false
        },
        {
          "id": "letter",
          "name": "书信",
          "type": "item",
          "aliases": [],
          "description": "书信的基础资料",
          "traits": "合成特征",
          "source": "user",
          "evidence": [
            {
              "blockId": "synthetic-block",
              "quote": "船灯"
            }
          ],
          "hiddenGoal": "",
          "knownFacts": [],
          "knownBy": [],
          "stackable": true
        }
      ],
      "lore": [
        {
          "id": "letter-fact",
          "title": "书信约定",
          "text": "书信约定次日见面。",
          "triggers": [
            "书信",
            "蓝舟"
          ],
          "entityIds": [
            "alice",
            "npc"
          ],
          "knownBy": [
            "alice"
          ],
          "source": "user",
          "evidence": []
        },
        {
          "id": "npc-secret",
          "title": "旅人私事",
          "text": "旅人打算独自去远城。",
          "triggers": [
            "旅人"
          ],
          "entityIds": [
            "npc"
          ],
          "knownBy": [
            "npc"
          ],
          "source": "user",
          "evidence": []
        }
      ],
      "initial": {
        "alice": {
          "alive": true,
          "location": "port",
          "inventory": {
            "letter": 2
          },
          "fields": {
            "money": 12,
            "navigation": 2
          },
          "relations": {
            "npc": "初识"
          },
          "goal": "把书信送达",
          "known": [
            "letter-fact"
          ]
        }
      },
      "unresolved": [
        "送信"
      ],
      "rules": {
        "genre": "mystery",
        "deathPolicy": "rescue",
        "relationshipNumbers": true,
        "randomChecks": true,
        "pace": "detailed",
        "style": "简洁的港口叙事",
        "customRules": "先记录账本，再叙述结果",
        "actionCosts": {
          "action": {
            "money": 1,
            "minutes": 5
          }
        },
        "abilityRequirements": {
          "action": {
            "navigation": 2
          }
        },
        "routes": {
          "port": [
            "far"
          ],
          "far": [
            "port"
          ]
        }
      }
    },
    "state": {
      "playerId": "alice",
      "minute": 30,
      "timeLabel": "午后",
      "location": "port",
      "entities": {
        "alice": {
          "alive": true,
          "location": "port",
          "inventory": {
            "letter": 2
          },
          "fields": {
            "money": 12,
            "navigation": 2,
            "hasPermit": false
          },
          "relations": {
            "npc": "初识"
          },
          "relationEvidence": {
            "npc": {
              "reason": "在渡口初次见面",
              "minute": 20,
              "checkpointId": "$before"
            }
          },
          "goal": "把书信送达",
          "known": [
            "letter-fact"
          ]
        },
        "npc": {
          "alive": true,
          "location": "port",
          "inventory": {},
          "fields": {
            "money": 8
          },
          "relations": {
            "alice": "谨慎"
          },
          "relationEvidence": {
            "alice": {
              "reason": "不认识来客",
              "minute": 20,
              "checkpointId": "$before"
            }
          },
          "goal": "私下筹备远行",
          "known": [
            "npc-secret"
          ]
        },
        "port": {
          "alive": null,
          "location": null,
          "inventory": {},
          "fields": {
            "weather": "晴"
          },
          "relations": {},
          "goal": "",
          "known": []
        },
        "far": {
          "alive": null,
          "location": null,
          "inventory": {},
          "fields": {},
          "relations": {},
          "goal": "",
          "known": []
        },
        "letter": {
          "alive": null,
          "location": null,
          "inventory": {},
          "fields": {},
          "relations": {},
          "goal": "",
          "known": []
        }
      },
      "known": [
        "letter-fact"
      ],
      "discovered": [
        "alice",
        "npc",
        "port",
        "letter"
      ],
      "critical": [
        "必须在明晚交信"
      ],
      "unresolved": [
        {
          "id": "letter-job",
          "description": "交信",
          "source": "initial"
        }
      ],
      "queue": [
        {
          "id": "private-arrival",
          "type": "background_progress",
          "entityId": "npc",
          "description": "旅人的私人接应",
          "visibleTo": [
            "npc"
          ],
          "audiencePlayerId": "alice",
          "audienceIds": [
            "npc"
          ],
          "audienceAll": false,
          "dueMinute": 150,
          "requiresDecision": false,
          "loreIds": [],
          "minute": 20,
          "scheduledAtMinute": 20
        },
        {
          "id": "choice",
          "type": "appointment",
          "entityId": "alice",
          "description": "明晚要不要赴约",
          "visibleTo": [
            "alice"
          ],
          "audiencePlayerId": "alice",
          "audienceIds": [
            "alice"
          ],
          "audienceAll": false,
          "dueMinute": 180,
          "requiresDecision": true,
          "decisionActorId": "alice",
          "loreIds": [],
          "minute": 20,
          "scheduledAtMinute": 20
        }
      ],
      "resolvedEvents": [
        "old-completed"
      ],
      "reachedEvents": [],
      "observed": {
        "npc": {
          "alive": true,
          "relation:alice": "谨慎",
          "relationEvidence:alice": {
            "reason": "不认识来客",
            "minute": 20,
            "checkpointId": "$before"
          },
          "field:money": 8
        }
      },
      "ended": false,
      "checks": [
        {
          "entityId": "alice",
          "ability": "navigation",
          "roll": 9,
          "result": "success"
        }
      ],
      "resolvedUnresolved": [
        "old-resolved-issue"
      ]
    },
    "memory": {
      "layers": [
        {
          "level": 1,
          "text": "早期记忆导航"
        }
      ],
      "pending": [
        {
          "checkpointId": "$before",
          "text": "此前承诺",
          "eventIds": [
            "event-before"
          ],
          "changes": []
        }
      ],
      "episodes": [
        {
          "firstCheckpointId": "$before",
          "lastCheckpointId": "$before",
          "text": "前档蓝舟导航",
          "eventIds": [
            "event-before"
          ]
        }
      ]
    },
    "title": "渡口的约定",
    "text": "午后，旅人站在渡口。你拿着两封书信。",
    "action": "我来到渡口",
    "options": [
      {
        "text": "交信",
        "kind": "action"
      }
    ],
    "changes": [
      {
        "field": "field:money",
        "before": 13,
        "after": 12,
        "reason": "渡口费"
      }
    ],
    "summary": "来到渡口"
  },
  "after": {
    "definition": {
      "version": 4,
      "title": "港口合成世界",
      "description": "仅供此次恢复验证",
      "narrativeStyle": "合成简洁",
      "timeLabel": "午后",
      "location": "port",
      "entities": [
        {
          "id": "alice",
          "name": "爱丽丝",
          "type": "character",
          "aliases": [
            "小艾"
          ],
          "description": "爱丽丝的基础资料",
          "traits": "获得通行资格",
          "source": "user",
          "evidence": [
            {
              "blockId": "synthetic-block",
              "quote": "船灯"
            }
          ],
          "hiddenGoal": "",
          "knownFacts": [
            "letter-fact"
          ],
          "knownBy": [],
          "stackable": false
        },
        {
          "id": "npc",
          "name": "旅人",
          "type": "character",
          "aliases": [],
          "description": "旅人的基础资料",
          "traits": "合成特征",
          "source": "user",
          "evidence": [
            {
              "blockId": "synthetic-block",
              "quote": "船灯"
            }
          ],
          "hiddenGoal": "私下筹备远行",
          "knownFacts": [],
          "knownBy": [
            "alice"
          ],
          "stackable": false
        },
        {
          "id": "port",
          "name": "港口",
          "type": "place",
          "aliases": [],
          "description": "港口的基础资料",
          "traits": "合成特征",
          "source": "user",
          "evidence": [
            {
              "blockId": "synthetic-block",
              "quote": "船灯"
            }
          ],
          "hiddenGoal": "",
          "knownFacts": [],
          "knownBy": [],
          "stackable": false
        },
        {
          "id": "far",
          "name": "远城",
          "type": "place",
          "aliases": [],
          "description": "远城的基础资料",
          "traits": "合成特征",
          "source": "user",
          "evidence": [
            {
              "blockId": "synthetic-block",
              "quote": "船灯"
            }
          ],
          "hiddenGoal": "",
          "knownFacts": [],
          "knownBy": [],
          "stackable": false
        },
        {
          "id": "letter",
          "name": "书信",
          "type": "item",
          "aliases": [],
          "description": "书信的基础资料",
          "traits": "合成特征",
          "source": "user",
          "evidence": [
            {
              "blockId": "synthetic-block",
              "quote": "船灯"
            }
          ],
          "hiddenGoal": "",
          "knownFacts": [],
          "knownBy": [],
          "stackable": true
        },
        {
          "id": "token",
          "name": "通行令",
          "type": "item",
          "aliases": [],
          "description": "通行令的基础资料",
          "traits": "合成特征",
          "source": "user",
          "evidence": [
            {
              "blockId": "synthetic-block",
              "quote": "船灯"
            }
          ],
          "hiddenGoal": "",
          "knownFacts": [],
          "knownBy": [],
          "stackable": false
        }
      ],
      "lore": [
        {
          "id": "letter-fact",
          "title": "书信约定",
          "text": "书信约定次日见面。",
          "triggers": [
            "书信",
            "蓝舟"
          ],
          "entityIds": [
            "alice",
            "npc"
          ],
          "knownBy": [
            "alice"
          ],
          "source": "user",
          "evidence": []
        },
        {
          "id": "npc-secret",
          "title": "旅人私事",
          "text": "旅人打算独自去远城。",
          "triggers": [
            "旅人"
          ],
          "entityIds": [
            "npc"
          ],
          "knownBy": [
            "npc"
          ],
          "source": "user",
          "evidence": []
        },
        {
          "id": "permit-fact",
          "title": "通行令知识",
          "text": "通行令让你进入远城。",
          "triggers": [
            "通行令"
          ],
          "entityIds": [
            "alice",
            "token"
          ],
          "knownBy": [
            "alice"
          ],
          "source": "game",
          "evidence": []
        }
      ],
      "initial": {
        "alice": {
          "alive": true,
          "location": "port",
          "inventory": {
            "letter": 2
          },
          "fields": {
            "money": 10,
            "navigation": 2
          },
          "relations": {
            "npc": "信任"
          },
          "goal": "赶上次日的船",
          "known": [
            "letter-fact",
            "permit-fact"
          ]
        }
      },
      "unresolved": [
        "送信"
      ],
      "rules": {
        "genre": "mystery",
        "deathPolicy": "rescue",
        "relationshipNumbers": true,
        "randomChecks": true,
        "pace": "detailed",
        "style": "简洁的港口叙事",
        "customRules": "渡口规则已修订，先核验通行令",
        "actionCosts": {
          "action": {
            "money": 1,
            "minutes": 5
          }
        },
        "abilityRequirements": {
          "action": {
            "navigation": 2
          }
        },
        "routes": {
          "port": [
            "far"
          ],
          "far": [
            "port"
          ]
        }
      }
    },
    "state": {
      "playerId": "alice",
      "minute": 90,
      "timeLabel": "傍晚",
      "location": "far",
      "entities": {
        "alice": {
          "alive": true,
          "location": "far",
          "inventory": {
            "letter": 1,
            "token": 1
          },
          "fields": {
            "money": 9,
            "navigation": 3,
            "hasPermit": true
          },
          "relations": {
            "npc": "信任"
          },
          "relationEvidence": {
            "npc": {
              "reason": "交出书信建立信任",
              "minute": 90,
              "checkpointId": "$after"
            }
          },
          "goal": "赶上次日的船",
          "known": [
            "letter-fact",
            "permit-fact"
          ]
        },
        "npc": {
          "alive": true,
          "location": "far",
          "inventory": {
            "letter": 1
          },
          "fields": {
            "money": 11
          },
          "relations": {
            "alice": "朋友"
          },
          "relationEvidence": {
            "alice": {
              "reason": "收到书信",
              "minute": 90,
              "checkpointId": "$after"
            }
          },
          "goal": "在远城联系同伴",
          "known": [
            "npc-secret",
            "letter-fact"
          ]
        },
        "port": {
          "alive": null,
          "location": null,
          "inventory": {},
          "fields": {
            "weather": "风起"
          },
          "relations": {},
          "goal": "",
          "known": []
        },
        "far": {
          "alive": null,
          "location": null,
          "inventory": {},
          "fields": {
            "curfew": false
          },
          "relations": {},
          "goal": "",
          "known": []
        },
        "letter": {
          "alive": null,
          "location": null,
          "inventory": {},
          "fields": {},
          "relations": {},
          "goal": "",
          "known": []
        },
        "token": {
          "alive": null,
          "location": null,
          "inventory": {},
          "fields": {
            "seal": "blue"
          },
          "relations": {},
          "goal": "",
          "known": []
        }
      },
      "known": [
        "letter-fact",
        "permit-fact"
      ],
      "discovered": [
        "alice",
        "npc",
        "port",
        "far",
        "letter",
        "token"
      ],
      "critical": [
        "必须在明晚交信",
        "书信已经交出一份"
      ],
      "unresolved": [
        {
          "id": "ship-job",
          "description": "找到次日的船",
          "source": "event-after"
        }
      ],
      "queue": [
        {
          "id": "private-arrival",
          "type": "background_progress",
          "entityId": "npc",
          "description": "旅人的私人接应",
          "visibleTo": [
            "npc"
          ],
          "audiencePlayerId": "alice",
          "audienceIds": [
            "npc"
          ],
          "audienceAll": false,
          "dueMinute": 150,
          "requiresDecision": false,
          "loreIds": [],
          "minute": 20,
          "scheduledAtMinute": 20
        },
        {
          "id": "choice",
          "type": "appointment",
          "entityId": "alice",
          "description": "明晚要不要赴约",
          "visibleTo": [
            "alice"
          ],
          "audiencePlayerId": "alice",
          "audienceIds": [
            "alice"
          ],
          "audienceAll": false,
          "dueMinute": 180,
          "requiresDecision": true,
          "decisionActorId": "alice",
          "loreIds": [],
          "minute": 20,
          "scheduledAtMinute": 20
        }
      ],
      "resolvedEvents": [
        "old-completed",
        "letter-job"
      ],
      "reachedEvents": [
        "deadline-letter"
      ],
      "observed": {
        "npc": {
          "alive": true,
          "relation:alice": "朋友",
          "relationEvidence:alice": {
            "reason": "收到书信",
            "minute": 90,
            "checkpointId": "$after"
          },
          "field:money": 11
        }
      },
      "ended": false,
      "checks": [
        {
          "entityId": "alice",
          "ability": "navigation",
          "roll": 12,
          "result": "success"
        }
      ],
      "resolvedUnresolved": [
        "old-resolved-issue",
        "letter-job"
      ]
    },
    "memory": {
      "layers": [
        {
          "level": 1,
          "text": "早期记忆导航"
        },
        {
          "level": 2,
          "text": "后期压缩导航"
        }
      ],
      "pending": [
        {
          "checkpointId": "$after",
          "text": "书信交出并取得通行令",
          "eventIds": [
            "event-after"
          ],
          "changes": [
            {
              "field": "inventory",
              "before": 2,
              "after": 1
            }
          ]
        }
      ],
      "episodes": [
        {
          "firstCheckpointId": "$before",
          "lastCheckpointId": "$before",
          "text": "前档蓝舟导航",
          "eventIds": [
            "event-before"
          ],
          "level": 2
        },
        {
          "firstCheckpointId": "$after",
          "lastCheckpointId": "$after",
          "text": "后档蓝舟导航",
          "eventIds": [
            "event-after"
          ]
        }
      ]
    },
    "title": "远城书信",
    "text": "傍晚，你在远城交出了一封书信，取得通行令。",
    "action": "我交出书信并进入远城",
    "options": [
      {
        "text": "询问次日的船",
        "kind": "dialogue"
      }
    ],
    "changes": [
      {
        "entityId": "alice",
        "field": "inventory",
        "before": {
          "letter": 2
        },
        "after": {
          "letter": 1,
          "token": 1
        },
        "reason": "交出一信并取得通行令"
      },
      {
        "entityId": "alice",
        "field": "field:money",
        "before": 12,
        "after": 9,
        "reason": "路费"
      }
    ],
    "summary": "书信交出并取得通行令"
  },
  "archives": {
    "before": "前档蓝舟原始档案：你在渡口答应交信，记录了具体约定。",
    "after": "后档蓝舟原始档案：书信交出并取得通行令，旅人答应次日带你登船。",
    "sibling": "废线蓝舟原始档案：这是未选择分支，不应召回。"
  },
  "events": {
    "before": "前档背景事件：港口燃起船灯",
    "after": "后档背景事件：旅人已经安排次日船位",
    "sibling": "废线背景事件：旅人去了另一座城"
  }
}''')


@pytest.fixture(autouse=True)
def require_mock_test_database():
    # Existing conftest migrates and resets only its explicitly dedicated _test DB.
    assert settings.ai_mode == 'mock'
    assert engine.url.database and engine.url.database.endswith('_test')


@pytest.fixture
def scenario():
    def forbidden(*args, **kwargs):
        raise AssertionError('A real provider is forbidden in this audit')

    calls = []
    original = MockAdapter.generate

    def generate(adapter, profile, prompt, data, limit):
        calls.append(prompt.splitlines()[0])
        result = original(adapter, profile, prompt, data, limit)
        if prompt.startswith('world-narrator'):
            result['text'] = '船灯在水面轻轻晃动。你把书信交给旅人，约定次日再到港口相会。这件事情已经发生，文字换了另一种表达。'
            result['usage']['outputTokens'] = len(result['text'])
        return result

    def stream(adapter, profile, prompt, data, limit, *, on_delta, cancel_check, **kwargs):
        result = generate(adapter, profile, prompt, data, limit)
        cancel_check()
        on_delta(result['text'])
        return result

    with patch.object(ChatCompletionsAdapter, 'generate', forbidden), patch.object(ChatCompletionsAdapter, 'generate_stream', forbidden), patch.object(MockAdapter, 'generate', generate), patch.object(MockAdapter, 'generate_stream', stream), TestClient(app, raise_server_exceptions=False) as client:
        username = 'restore_' + uuid4().hex[:18]
        registered = client.post('/api/reader/v1/auth/register', json={'username': username, 'password': 'synthetic-restore-password-123'})
        assert registered.status_code == 201, registered.text
        logged = client.post('/api/reader/v1/auth/login', json={'username': username, 'password': 'synthetic-restore-password-123', 'clientType': 'android', 'deviceName': 'isolated-full-restore'})
        assert logged.status_code == 200, logged.text
        client.headers['Authorization'] = 'Bearer ' + logged.json()['accessToken']
        owner = logged.json()['user']['id']
        profile = client.post('/api/reader/v1/model-profiles', json={'name': 'synthetic mock only', 'baseUrl': 'mock://deterministic', 'modelId': 'fixture-v1', 'key': 'TEST-KEY-NOT-REAL', 'contextWindow': 32768, 'callBudget': 30, 'inputBudget': 10000000, 'outputBudget': 1000000})
        assert profile.status_code == 201, profile.text
        ids = {k: uuid4().hex for k in ('world', 'line', 'before', 'after', 'sibling', 'archiveBefore', 'archiveAfter', 'archiveSibling', 'eventBefore', 'eventAfter', 'eventSibling')}
        fixture_text = json.dumps(EXPECTED, ensure_ascii=False)
        for key, value in ids.items():
            fixture_text = fixture_text.replace('$'+key, value)
        fixtures = json.loads(fixture_text)
        with SessionLocal() as db:
            world = World(id=ids['world'], owner_id=owner, name='完整恢复合成世界', source_title='合成来源', source_book_id=None,
                          source={'anchor': {}, 'title': '合成来源', 'blocks': [{'blockId': 'synthetic-block', 'text': '船灯与港口书信都是本次合成测试资料。'}]},
                          definition=deepcopy(fixtures['after']['definition']), status='active', active_line_id=ids['line'])
            db.add(world)
            db.flush()
            line = WorldLine(id=ids['line'], owner_id=owner, world_id=world.id, name='原线', head_checkpoint_id=ids['after'])
            db.add(line)
            db.flush()
            for label, parent, sequence in [('before', None, 4), ('after', ids['before'], 5), ('sibling', ids['before'], 5)]:
                source = fixtures['before' if label == 'before' else 'after']
                cp = WorldCheckpoint(id=ids[label], owner_id=owner, world_id=world.id, line_id=line.id,
                    parent_checkpoint_id=parent, sequence=sequence, title=source['title'], text=source['text'], action=source['action'],
                    state=deepcopy(source['state']), definition=deepcopy(source['definition']), memory=deepcopy(source['memory']),
                    options=deepcopy(source['options']), changes=deepcopy(source['changes']), summary=source['summary'], kind='turn')
                cp.content_hash = checkpoint_hash(cp.state, cp.definition, cp.text, parent)
                db.add(cp)
            db.flush()
            for label in ('Before', 'After', 'Sibling'):
                lower = label.lower()
                db.add(WorldMemory(id=ids['archive'+label], owner_id=owner, world_id=world.id, line_id=line.id,
                    checkpoint_id=ids[lower], first_checkpoint_id=ids[lower], last_checkpoint_id=ids[lower],
                    text=EXPECTED['archives'][lower], event_ids=['event-'+lower], level=1))
                db.add(WorldMemory(id=uuid4().hex, owner_id=owner, world_id=world.id, line_id=line.id,
                    checkpoint_id=ids[lower], first_checkpoint_id=ids[lower], last_checkpoint_id=ids[lower],
                    text='二级导航摘要-'+lower, event_ids=['event-'+lower], level=2))
                data = {'id': 'event-'+lower, 'type': 'background_progress', 'entityId': 'npc', 'description': EXPECTED['events'][lower],
                        'visibleTo': ['player'], 'audiencePlayerId': 'alice', 'audienceIds': ['alice'], 'audienceAll': False, 'minute': 30 if lower == 'before' else 90}
                db.add(WorldEvent(id=ids['event'+label], owner_id=owner, world_id=world.id, checkpoint_id=ids[lower], entity_id='npc', type='background_progress', data=data, visible_to=['alice'], minute=data['minute']))
                if lower == 'after':
                    private = {'id': 'private-npc-event', 'type': 'background_progress', 'entityId': 'npc', 'description': '只有旅人知道的隐秘后台进展',
                               'visibleTo': ['npc'], 'audiencePlayerId': 'alice', 'audienceIds': ['npc'], 'audienceAll': False, 'minute': 90}
                    db.add(WorldEvent(id=uuid4().hex, owner_id=owner, world_id=world.id, checkpoint_id=ids[lower], entity_id='npc', type='background_progress', data=private, visible_to=['npc'], minute=90))
            db.commit()
        yield client, owner, ids, calls, fixtures


def raw(cp):
    return {k: deepcopy(getattr(cp, k)) for k in ('state', 'definition', 'memory', 'title', 'text', 'action', 'options', 'changes', 'summary', 'content_hash', 'parent_checkpoint_id', 'sequence', 'kind')}


def archive_content(record):
    return (record.owner_id, record.world_id, record.first_checkpoint_id,
            record.last_checkpoint_id, record.level, record.text,
            deepcopy(record.event_ids), record.created_at)


def event_content(record):
    return (record.owner_id, record.world_id, record.entity_id, record.type,
            deepcopy(record.data), deepcopy(record.visible_to), record.minute, record.created_at)


@pytest.mark.parametrize('mode', ['continue', 'rechoose', 'rewrite'])
def test_complete_restore_contract(scenario, mode):
    client, owner, ids, calls, fixtures = scenario
    target = 'before' if mode == 'rechoose' else 'after'
    expected = fixtures[target]
    with SessionLocal() as db:
        frozen_before = raw(db.get(WorldCheckpoint, ids['before']))
        frozen_after = raw(db.get(WorldCheckpoint, ids['after']))
        frozen_archives = [(m.id, m.checkpoint_id, m.level, m.text, m.event_ids) for m in db.scalars(select(WorldMemory).where(WorldMemory.owner_id == owner).order_by(WorldMemory.id))]
        original_archive_ids = [m[0] for m in frozen_archives]
        original_event_rows = [(e.id, e.checkpoint_id, e.data, e.visible_to, e.minute) for e in db.scalars(select(WorldEvent).where(WorldEvent.owner_id == owner).order_by(WorldEvent.id))]
        selected_archives = sorted([archive_content(m) for m in db.scalars(select(WorldMemory).where(WorldMemory.owner_id == owner, WorldMemory.checkpoint_id == ids['after']))], key=lambda m: m[4])
        selected_events = sorted([event_content(e) for e in db.scalars(select(WorldEvent).where(WorldEvent.owner_id == owner, WorldEvent.checkpoint_id == ids['after']))], key=lambda e: e[4]['id'])
        original_line = db.get(WorldLine, ids['line'])
        original_head, original_version = original_line.head_checkpoint_id, original_line.version
        usage_before = db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.owner_id == owner))
    url = f'/worlds/{ids["world"]}/rewrite' if mode == 'rewrite' else f'/worlds/{ids["world"]}/fork'
    body = {'checkpointId': ids['after'], 'instruction': '保留已经发生的结果，换一种文字表达'} if mode == 'rewrite' else {'checkpointId': ids['after'], 'mode': mode, 'name': '完整恢复-'+mode}
    response = client.post(API + url, json=body, headers={'Idempotency-Key': 'full-restore-'+mode})
    assert response.status_code == (202 if mode == 'rewrite' else 200), response.text
    result = response.json()
    if mode == 'rewrite':
        process(result['taskId'])
    line_id = result['lineId'] if mode == 'rewrite' else result['id']
    line_read = client.get(API + f'/worlds/{ids["world"]}/lines/{line_id}')
    assert line_read.status_code == 200, line_read.text
    with SessionLocal() as db:
        restored = db.get(WorldCheckpoint, line_read.json()['headCheckpointId'])
        current = db.get(World, ids['world'])
        actual_context = context(db, restored, current, '请回忆蓝舟的书信和港口背景')
        expected_labels = {'before'} if mode == 'rechoose' else {'before', 'after'}
        expected_archives = sorted(EXPECTED['archives'][name] for name in expected_labels)
        public_expected_events = sorted(EXPECTED['events'][name] for name in expected_labels)
        expected_events = sorted(public_expected_events + (['只有旅人知道的隐秘后台进展'] if target == 'after' else []))
        archives_actual = sorted(m['text'] for m in actual_context['recalledEpisodes'])
        events_actual = sorted(e['description'] for e in actual_context['recalledEvents'])
        path_ids, walk = [], restored
        while walk:
            path_ids.append(walk.id)
            walk = db.get(WorldCheckpoint, walk.parent_checkpoint_id) if walk.parent_checkpoint_id else None
        active_archive_rows = list(db.scalars(select(WorldMemory).where(WorldMemory.owner_id == owner, WorldMemory.checkpoint_id.in_(path_ids))))
        active_archives = sorted((m.level, m.text, m.first_checkpoint_id, m.last_checkpoint_id, m.event_ids) for m in active_archive_rows)
        active_expected = sorted((level, EXPECTED['archives'][name] if level == 1 else '二级导航摘要-'+name, ids[name], ids[name], ['event-'+name]) for name in expected_labels for level in (1, 2))
        new_archive_rows = list(db.scalars(select(WorldMemory).where(WorldMemory.owner_id == owner, WorldMemory.id.notin_(original_archive_ids))))
        new_event_rows = list(db.scalars(select(WorldEvent).where(WorldEvent.owner_id == owner, WorldEvent.id.notin_([e[0] for e in original_event_rows]))))
        raw_result = raw(restored)
        observation = {'mode': mode, 'ownerId': owner, 'ids': ids, 'selectedExpected': target,
            'stateEqual': restored.state == expected['state'], 'definitionEqual': restored.definition == expected['definition'], 'memoryEqual': restored.memory == expected['memory'],
            'rawResult': raw_result, 'expected': expected, 'context': actual_context,
            'archivesExpected': expected_archives, 'archivesActual': archives_actual,
            'eventsExpected': expected_events, 'eventsActual': events_actual, 'mockStageCalls': calls,
            'activeArchiveRowsExpected': active_expected, 'activeArchiveRowsActual': active_archives,
            'newArchives': [{'id': m.id, 'checkpointId': m.checkpoint_id, 'lineId': m.line_id, 'level': m.level, 'text': m.text} for m in new_archive_rows],
            'newEvents': [{'id': e.id, 'checkpointId': e.checkpoint_id, 'data': e.data, 'minute': e.minute} for e in new_event_rows],
            'originalSnapshotsUnchanged': raw(db.get(WorldCheckpoint, ids['before'])) == frozen_before and raw(db.get(WorldCheckpoint, ids['after'])) == frozen_after,
            'durableArchiveRowsUnchanged': [(m.id, m.checkpoint_id, m.level, m.text, m.event_ids) for m in db.scalars(select(WorldMemory).where(WorldMemory.owner_id == owner, WorldMemory.id.in_(original_archive_ids)).order_by(WorldMemory.id))] == frozen_archives,
            'originalEventRowsUnchanged': [(e.id, e.checkpoint_id, e.data, e.visible_to, e.minute) for e in db.scalars(select(WorldEvent).where(WorldEvent.owner_id == owner, WorldEvent.id.in_([e[0] for e in original_event_rows])).order_by(WorldEvent.id))] == original_event_rows}
        if ARTIFACT_DIR:
            output = Path(ARTIFACT_DIR)
            output.mkdir(parents=True, exist_ok=True)
            (output / ('observed-'+mode+'.json')).write_text(json.dumps(observation, ensure_ascii=False, indent=2), encoding='utf-8')
        assert restored.state == expected['state'], 'complete raw state differed'
        assert restored.definition == expected['definition'], 'complete definition/rules/entity/lore differed'
        assert restored.memory == expected['memory'], 'complete pending/episodes/layers differed'
        assert observation['originalSnapshotsUnchanged'] and observation['durableArchiveRowsUnchanged'] and observation['originalEventRowsUnchanged']
        assert all(m.checkpoint_id == restored.id and m.line_id == restored.line_id for m in new_archive_rows)
        assert all(e.checkpoint_id == restored.id and e.world_id == restored.world_id for e in new_event_rows)
        line = db.get(WorldLine, ids['line'])
        assert (line.head_checkpoint_id, line.version) == (original_head, original_version)
        assert EXPECTED['archives']['sibling'] not in archives_actual and EXPECTED['events']['sibling'] not in events_actual
        assert restored.content_hash == checkpoint_hash(restored.state, restored.definition, restored.text, restored.parent_checkpoint_id)
        if mode == 'rewrite':
            task = db.get(Task, result['taskId'])
            assert (task.state, task.persistence_state) == ('succeeded', 'saved')
            assert restored.parent_checkpoint_id == ids['before'] and restored.kind == 'rewrite'
            assert restored.action == expected['action'] and restored.changes == expected['changes'] and restored.options == expected['options']
            assert restored.title == expected['title'] and restored.summary == expected['summary']
            assert restored.sequence == 5
            assert restored.text != expected['text'] and len(calls) == 2
            assert db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.owner_id == owner)) - usage_before == 2
            assert sorted([archive_content(m) for m in new_archive_rows], key=lambda m: m[4]) == selected_archives
            assert sorted([event_content(e) for e in new_event_rows], key=lambda e: e[4]['id']) == selected_events
        else:
            assert restored.parent_checkpoint_id == ids[target] and restored.kind == 'fork'
            assert restored.text == expected['text'] and restored.action == '' and restored.changes == []
            assert restored.options == expected['options'] and restored.sequence == (4 if mode == 'rechoose' else 5)
            assert restored.title == '完整恢复-'+mode and restored.summary == expected['summary']
            assert calls == [] and db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.owner_id == owner)) == usage_before
            assert new_archive_rows == [] and new_event_rows == []
        assert archives_actual == expected_archives, 'same-result restoration lost an addressable level-1 archive'
        assert active_archives == active_expected, 'same-result restoration lost a level-1/level-2 archive or its checkpoint range/event IDs'
        assert events_actual == expected_events, 'same-result restoration lost background event history'
        # Internal director recall may retain an NPC's history; player-facing API
        # must still obey the frozen audience and cannot grant that knowledge.
        event_read = client.get(API + f'/worlds/{ids["world"]}/checkpoints/{restored.id}/events')
        assert event_read.status_code == 200, event_read.text
        assert sorted(e['description'] for e in event_read.json()['items']) == public_expected_events
        assert '只有旅人知道的隐秘后台进展' not in line_read.text + event_read.text
        assert 'npc-secret' not in restored.state['known']
    if mode == 'rewrite':
        calls_after = list(calls)
        with SessionLocal() as db:
            row_counts = tuple(db.scalar(select(func.count()).select_from(model).where(model.owner_id == owner)) for model in (WorldCheckpoint, WorldMemory, WorldEvent, UsageRecord))
        replay = client.post(API + url, json=body, headers={'Idempotency-Key': 'full-restore-'+mode})
        assert replay.status_code == 202 and replay.json() == result
        process(result['taskId'])
        with SessionLocal() as db:
            assert row_counts == tuple(db.scalar(select(func.count()).select_from(model).where(model.owner_id == owner)) for model in (WorldCheckpoint, WorldMemory, WorldEvent, UsageRecord))
        assert calls == calls_after
