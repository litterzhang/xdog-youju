"""Small persistent rule cache; immutable content store plus a unique SQLite receipt."""

import json

from sqlalchemy.orm import Session

from youju.models.onboarding import ApplicationRuleReceipt

from .periods import EVALUATOR_VERSION, rule_key, validate_period
from .store import digest, json_bytes, toolkit_key


class RuleCache:
    def __init__(self, store):
        self.store = store

    def get(self, bundle, adapter_digest):
        key = rule_key(
            bundle, adapter_digest=digest({"candidate": adapter_digest, "checker": toolkit_key()})
        )
        with self.store.lock, Session(self.store.engine) as session:
            receipt = session.get(ApplicationRuleReceipt, key)
            if receipt is None or receipt.evaluator_version != EVALUATOR_VERSION:
                return None
            period = json.loads(self.store.blobs.read(receipt.rule_ref))
        if validate_period(bundle, period):
            raise ValueError("Cached application rule failed independent validation")
        return period

    def put(self, bundle, adapter_digest, period):
        if validate_period(bundle, period):
            raise ValueError("Only independently checked application rules may be cached")
        key = rule_key(
            bundle, adapter_digest=digest({"candidate": adapter_digest, "checker": toolkit_key()})
        )
        reference = self.store.blobs.put(json_bytes(period))
        with self.store.lock, Session(self.store.engine) as session, session.begin():
            old = session.get(ApplicationRuleReceipt, key)
            if old is None:
                session.add(
                    ApplicationRuleReceipt(
                        key=key, rule_ref=reference, evaluator_version=EVALUATOR_VERSION
                    )
                )
            elif old.rule_ref != reference:
                raise ValueError("Application rule receipt cannot be silently overwritten")
