"""Task-pinned case catalog and digest-bound tests, using existing immutable blobs.

Bodies are not prompt inputs or writable mounts. The catalog contains references,
not paths/URLs that can be fetched. No caller-supplied gold becomes acceptance truth.
"""

import json
import re

from youju.recipe_runtime.contracts import candidate_digest
from youju.recipe_runtime.versioning import runtime_fingerprint as toolkit_key
from youju.serialization import digest, json_bytes

CATALOG_VERSION = "recipe-cases-v1"
CHECKER_VERSION = "recipe-checks-v1"
MAX_CASES = 5000
MAX_CATALOG_BYTES = 2 * 1024 * 1024
MAX_CASE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
PAGE_SIZE = 20
READ_CHARS = 12000


def freeze_cases(blobs, source_id, bundles):
    """Host preparation writes content-addressed bodies outside the prompt manifest."""
    cases, total, seen = [], 0, set()
    for bundle in bundles:
        if bundle.get("source_id") != source_id or not bundle.get("id") or bundle["id"] in seen:
            raise ValueError("Case identity must be unique and belong to the task source")
        body = json_bytes(bundle)
        total += len(body)
        if len(cases) >= MAX_CASES or len(body) > MAX_CASE_BYTES or total > MAX_TOTAL_BYTES:
            raise ValueError("Frozen case catalog exceeds its explicit resource limit")
        seen.add(bundle["id"])
        cases.append(
            {
                "case_id": bundle["id"],
                "body_ref": blobs.put(body),
                "bytes": len(body),
                "split": "development",
                "formats": sorted(
                    {
                        doc.get("format", "unknown")
                        for doc in [bundle["page"], *bundle.get("attachments", [])]
                    }
                ),
                "material_status": bundle.get("status", "unknown"),
            }
        )
    catalog = {"schema_version": CATALOG_VERSION, "source_id": source_id, "cases": cases}
    body = json_bytes(catalog)
    if len(body) > MAX_CATALOG_BYTES:
        raise ValueError("Frozen case index exceeds its byte limit")
    return blobs.put(body)


def load_catalog(blobs, reference, source_id):
    body = blobs.read(reference)
    if len(body) > MAX_CATALOG_BYTES:
        raise ValueError("Case index byte limit")
    catalog = json.loads(body)
    if (
        set(catalog) != {"schema_version", "source_id", "cases"}
        or catalog["schema_version"] != CATALOG_VERSION
        or catalog["source_id"] != source_id
        or not isinstance(catalog["cases"], list)
        or len(catalog["cases"]) > MAX_CASES
    ):
        raise ValueError("Invalid task case catalog")
    seen, total = set(), 0
    for case in catalog["cases"]:
        if (
            set(case) != {"case_id", "body_ref", "bytes", "split", "formats", "material_status"}
            or not isinstance(case["case_id"], str)
            or case["case_id"] in seen
            or len(case["case_id"]) > 128
            or case["split"] != "development"
            or not re.fullmatch(r"[0-9a-f]{64}", case["body_ref"])
            or type(case["bytes"]) is not int
            or not 0 < case["bytes"] <= MAX_CASE_BYTES
        ):
            raise ValueError("Invalid frozen case identity")
        total += case["bytes"]
        seen.add(case["case_id"])
    if total > MAX_TOTAL_BYTES:
        raise ValueError("Case catalog aggregate limit")
    return catalog


def read_case(blobs, case, source_id):
    body = blobs.read(case["body_ref"])
    if len(body) != case["bytes"] or len(body) > MAX_CASE_BYTES:
        raise ValueError("Pinned case size changed")
    bundle = json.loads(body)
    if bundle.get("id") != case["case_id"] or bundle.get("source_id") != source_id:
        raise ValueError("Pinned case source or identity mismatch")
    return bundle


class CaseAccess:
    """One assigned task, bounded access and immutable host test receipts."""

    def __init__(
        self,
        blobs,
        *,
        catalog_ref,
        source_id,
        input_revision,
        now_iso,
        model_profile_digest,
        author,
        workspace,
        failure_refs=None,
    ):
        self.blobs, self.source_id = blobs, source_id
        self.catalog = load_catalog(blobs, catalog_ref, source_id)
        self.cases = {c["case_id"]: c for c in self.catalog["cases"]}
        self.failure_refs = validate_failure_inputs(
            blobs, self.catalog, {} if failure_refs is None else failure_refs
        )
        self.bindings = {
            "catalog_digest": catalog_ref,
            "input_revision": input_revision,
            "frozen_clock": now_iso,
            "model_profile_digest": model_profile_digest,
            "checker_version": CHECKER_VERSION + ":" + toolkit_key(),
        }
        self.author, self.workspace, self.now_iso = author, workspace, now_iso
        if self.author.workspace is None:
            self.author.workspace = workspace
        elif self.author.workspace.root != workspace.root:
            raise ValueError("Diagnostic workspace must be assigned to this case task")
        self.receipts = {}

    @staticmethod
    def _cursor(cursor, length):
        if type(cursor) is not int or not 0 <= cursor <= length:
            raise ValueError("Invalid page cursor")

    def list_cases(self, cursor=0):
        cases = self.catalog["cases"]
        self._cursor(cursor, len(cases))
        return {
            "cases": [
                {
                    **{key: value for key, value in c.items() if key != "body_ref"},
                    "saved_failures": [
                        "source-failure:" + ref for ref in self.failure_refs.get(c["case_id"], [])
                    ],
                }
                for c in cases[cursor : cursor + PAGE_SIZE]
            ],
            "next_cursor": cursor + PAGE_SIZE if cursor + PAGE_SIZE < len(cases) else None,
            "total": len(cases),
            "catalog_digest": self.bindings["catalog_digest"],
        }

    def read_case(self, case_id, cursor=0):
        case = self.cases.get(case_id)
        if case is None:
            raise ValueError("Case is not in the assigned task catalog")
        text = json.dumps(read_case(self.blobs, case, self.source_id), ensure_ascii=False)
        self._cursor(cursor, len(text))
        return {
            "case_id": case_id,
            "material_digest": case["body_ref"],
            "untrusted_material": text[cursor : cursor + READ_CHARS],
            "next_cursor": cursor + READ_CHARS if cursor + READ_CHARS < len(text) else None,
        }

    async def test_recipe(self, case_ids):
        if (
            not isinstance(case_ids, list)
            or not 1 <= len(case_ids) <= PAGE_SIZE
            or any(case_id not in self.cases for case_id in case_ids)
            or len(set(case_ids)) != len(case_ids)
        ):
            raise ValueError("Test one to twenty unique task case IDs")
        candidate = self.workspace.load_candidate()
        binding = {
            **self.bindings,
            "candidate_digest": candidate_digest(candidate),
            "case_versions": [[key, self.cases[key]["body_ref"]] for key in case_ids],
        }
        test_id = digest(binding)
        if test_id not in self.receipts:
            saved = self.workspace.load_test(test_id)
            if saved is not None:
                if any(saved.get(key) != value for key, value in binding.items()):
                    raise ValueError("Stale or mismatched test receipt")
                self.receipts[test_id] = saved
        if test_id in self.receipts:
            return self._summary(self.receipts[test_id], cached=True)
        samples = [read_case(self.blobs, self.cases[key], self.source_id) for key in case_ids]
        targets, non_targets = [], []
        for sample in samples:
            assessment = sample.get("assessment")
            if assessment is not None and (
                assessment.get("business_type") != "recruitment"
                or not {"article", "attachment"}.intersection(assessment.get("page_roles", []))
            ):
                non_targets.append(
                    {
                        "resource_id": sample.get("resource_id"),
                        "status": "not_extraction_target",
                        "classification_tested": False,
                    }
                )
            else:
                targets.append(sample)
        attempt = await self.author.validate_candidate(
            candidate,
            targets,
            self.now_iso,
            1,
            require_application_period=True,
            validation_bindings=binding,
        )
        attempt["samples"].extend(non_targets)
        if not targets and not attempt["errors"]:
            attempt["status"] = "not_applicable"
        diagnostic_body = json_bytes(
            {
                "errors": attempt["errors"],
                "samples": attempt["samples"],
                "failures": attempt.get("failures", []),
            }
        )
        if len(diagnostic_body) > MAX_CASE_BYTES:
            raise ValueError("Test diagnostics exceed their byte limit; test fewer cases")
        receipt = {
            "test_id": test_id,
            **binding,
            "status": attempt["status"],
            "diagnostic_ref": self.blobs.put(diagnostic_body),
            "error_count": len(attempt["errors"]),
            "sample_count": len(attempt["samples"]),
            "semantic_status": "unlabeled_not_accuracy_evidence",
        }
        # Append-only feedback lives in existing history, separate from published rows.
        self.workspace.record_test(receipt)
        self.receipts[test_id] = receipt
        return self._summary(receipt)

    @staticmethod
    def _summary(receipt, cached=False):
        return {
            key: receipt[key]
            for key in ("test_id", "candidate_digest", "status", "semantic_status")
        } | {
            "cached": cached,
            "error_count": receipt["error_count"],
            "sample_count": receipt["sample_count"],
            "detail_cursor": 0,
        }

    def inspect_failure(self, test_id, cursor=0, *, section="issues", issue_index=0, limit=20):
        from youju.onboarding.diagnostics import page_record

        if test_id.startswith("source-failure:"):
            reference = test_id[len("source-failure:") :]
            if not any(reference in values for values in self.failure_refs.values()):
                raise ValueError("Source failure is not assigned to this task")
            record = json.loads(self.blobs.read(reference))
            return {
                "failure_id": reference,
                **page_record(
                    record, section=section, cursor=cursor, issue_index=issue_index, limit=limit
                ),
            }
        if test_id.startswith("failure:"):
            failure_id = test_id[len("failure:") :]
            record = self.workspace.load_failure(failure_id)
            binding = record.get("bindings", {})
            case = self.cases.get(binding.get("case_id"))
            if (
                case is None
                or binding.get("case_version") != case["body_ref"]
                or binding.get("input_revision") != self.bindings["input_revision"]
                or binding.get("frozen_clock") != self.now_iso
                or binding.get("model_profile_digest") != self.bindings["model_profile_digest"]
                or binding.get("checker_version")
                not in {toolkit_key(), self.bindings["checker_version"]}
            ):
                raise ValueError("Rejected output is not bound to this task/case/checker")
            return {
                "failure_id": failure_id,
                **page_record(
                    record, section=section, cursor=cursor, issue_index=issue_index, limit=limit
                ),
            }
        receipt = self.receipts.get(test_id)
        if receipt is None:
            receipt = self.workspace.load_test(test_id)
            if receipt is None or any(receipt.get(k) != v for k, v in self.bindings.items()):
                raise ValueError("Test is not from this assigned task")
            self.receipts[test_id] = receipt
        body = self.blobs.read(receipt["diagnostic_ref"])
        if len(body) > MAX_CASE_BYTES:
            raise ValueError("Test diagnostic size mismatch")
        diagnostic = json.loads(body)
        record = {
            "error_code": "TEST_VALIDATION",
            "bindings": {
                key: value for key, value in receipt.items() if key not in {"diagnostic_ref"}
            },
            "stage": "test",
            "issues": {"available": True, "truncated": False, "value": diagnostic["errors"]},
        }
        if section in {
            "rejected_output",
            "partial_output",
            "script_draft",
            "detailed_draft",
            "merged_draft",
        }:
            raise ValueError("Select a failure_id to inspect original rejected output")
        return {
            "test_id": test_id,
            "candidate_digest": receipt["candidate_digest"],
            "failures": diagnostic.get("failures", []),
            **page_record(
                record, section=section, cursor=cursor, issue_index=issue_index, limit=limit
            ),
        }


def validate_failure_inputs(blobs, catalog, inputs):
    """Accept only frozen saved failures for cases already in the task's catalog."""
    from youju.onboarding.diagnostics import FAILURE_FORMAT, MAX_FAILURE_BYTES

    if not isinstance(inputs, dict) or len(inputs) > MAX_CASES:
        raise ValueError("Invalid task failure map")
    cases = {case["case_id"]: case for case in catalog["cases"]}
    total = 0
    for case_id, references in inputs.items():
        if case_id not in cases or not isinstance(references, list) or len(references) > 20:
            raise ValueError("Failure inputs exceed their authorized case scope")
        for reference in references:
            body = blobs.read(reference)
            total += len(body)
            if len(body) > MAX_FAILURE_BYTES or total > MAX_TOTAL_BYTES:
                raise ValueError("Failure input byte limit")
            record = json.loads(body)
            binding = record.get("bindings", {})
            if (
                record.get("format") != FAILURE_FORMAT
                or binding.get("source_id") != catalog["source_id"]
                or binding.get("case_id") != case_id
                or binding.get("case_version") != cases[case_id]["body_ref"]
            ):
                raise ValueError("Failure input material/source binding mismatch")
    return inputs
