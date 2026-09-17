"""Durable per-run accounting. Missing billing data is never zero."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import uuid


def save(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def initialize(root, complete_history=True):
    path = root / 'agent-usage.json'
    if not path.exists():
        save(path, {'version': 1, 'complete_history': complete_history, 'events': []})
    return path


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def start(path, provider, model, operation):
    if path is None:
        return None
    data = json.loads(path.read_text(encoding='utf-8'))
    identifier = uuid.uuid4().hex
    data['events'].append({'id': identifier, 'at': datetime.now(timezone.utc).isoformat(),
                           'provider': provider, 'model': model, 'operation': operation,
                           'status': 'pending', 'cost_usd': None})
    save(path, data)  # Written before sending: a crash cannot erase an attempt.
    return identifier


def finish(path, identifier, status, usage, seconds):
    if identifier is None:
        return
    data = json.loads(path.read_text(encoding='utf-8'))
    row = next(row for row in data['events'] if row['id'] == identifier)
    row.update(status=status, seconds=seconds)
    usage = usage if isinstance(usage, dict) else {}
    for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
        row[key] = number(usage.get(key))
    # OpenRouter documents usage.cost as the account charge in USD credits:
    # https://openrouter.ai/docs/cookbook/administration/usage-accounting
    cost = number(usage.get('cost_usd'))
    if cost is None and (row['provider'] == 'openrouter.ai' or usage.get('currency') == 'USD'):
        cost = number(usage.get('cost'))
    row['cost_usd'] = cost
    save(path, data)


def summary(root):
    path = root / 'agent-usage.json'
    if not path.exists():
        return {'calls': None, 'cost_usd': None, 'known_cost_usd': 0, 'unknown_cost_calls': None,
                'complete_history': False, 'events': []}
    data = json.loads(path.read_text(encoding='utf-8'))
    events = data['events']
    costs = [number(row.get('cost_usd')) for row in events]
    known = round(sum(cost for cost in costs if cost is not None), 10)
    unknown = sum(cost is None for cost in costs)
    return {'calls': len(events), 'cost_usd': known if not unknown and data['complete_history'] else None,
            'known_cost_usd': known, 'unknown_cost_calls': unknown, 'complete_history': data['complete_history'],
            'prompt_tokens': sum(row.get('prompt_tokens') or 0 for row in events),
            'completion_tokens': sum(row.get('completion_tokens') or 0 for row in events), 'events': events}


@contextmanager
def metered(agent, root):
    if agent is None or not hasattr(agent, 'usage_path'):
        yield
        return
    previous = agent.usage_path
    agent.usage_path = initialize(root, complete_history=False)
    try:
        yield
    finally:
        agent.usage_path = previous
