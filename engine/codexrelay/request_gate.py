"""Experiment authorization at the shared Python provider boundary, each attempt."""
import json
import uuid
from decimal import Decimal
from .usage import estimate


class ExperimentRequestGate:
    def __init__(self, store, task):
        self.store, self.task = store, task

    def reserve(self, config, messages, tools):
        from .evaluation import Evaluation
        # A conservative local estimate is a reservation, never reported usage.
        size = len(json.dumps({'messages': messages, 'tools': tools}, ensure_ascii=True).encode('utf-8'))
        # Serialized ASCII bytes are an upper reservation bound, not metering.
        prompt = size
        tokens = prompt + config.max_output_tokens
        evaluation = Evaluation(self.store)
        # Authorization and settlement use the same frozen experiment prices.
        price = evaluation.describe(self.task['experiment_id'])['conditions']['external_price']
        cost = estimate({'prompt': prompt, 'completion': config.max_output_tokens}, price) if price else None
        if price and price.get('cache_write') is not None and price.get('cached_input') is not None:
            written = Decimal(prompt) * Decimal(price['cache_write']) / Decimal(1_000_000)
            cost = (cost[0], cost[1] + written)
        else:
            # A finite monetary authorization cannot assume an unknown charge is zero.
            cost = None
        identifier = uuid.uuid4().hex
        evaluation.reserve_request(experiment_id=self.task['experiment_id'], trial_id=self.task['trial_id'],
            request_id=identifier, group_id=self.task['group_id'], run_id=self.task['id'], estimated_tokens=tokens,
            estimated_cost=format(cost[1], 'f') if cost else None,
            request_kind='probe' if self.task.get('purpose') == 'probe' else config.request_kind)
        return identifier

    def settle(self, identifier, response):
        from .evaluation import Evaluation
        usage = response.usage if response else None
        counters = None
        if usage:
            counters = dict(prompt=usage.prompt_tokens, completion=usage.completion_tokens,
                            cached_input=usage.cached_input_tokens, cache_write_input=usage.cache_write_input_tokens)
        Evaluation(self.store).settle_request(request_id=identifier,
            usage_tokens=usage.prompt_tokens + usage.completion_tokens if usage else None,
            actual_cost=None, usage=counters)
