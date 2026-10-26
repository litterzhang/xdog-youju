"""Short SQLite transactions for resumable source acquisition and material ledgers."""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import RLock
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from youju.crawlers.policy import origin
from youju.models import (
    Capture,
    CrawlJob,
    Discovery,
    DiscoveryMethod,
    FetchAttempt,
    FetchOutcome,
    MaterialDocument,
    OrganizationScope,
    PageAssessment,
    RegistryStatus,
    Resource,
    ResourceBundle,
    ResourceKind,
    ResourceScan,
    ResourceVersion,
    ReviewStatus,
    ScanPolicy,
    Source,
    SourcePipeline,
    SourceRecipeBinding,
)
from youju.models.base import normalize_url, utc_now
from youju.poc.scope import DiscoveryScope
from youju.poc.storage import BlobStore
from youju.recipe_runtime.versioning import runtime_fingerprint
from youju.serialization import digest as digest
from youju.serialization import json_bytes as json_bytes

from .classification_policy import scheduled

MATERIAL_PARSER_KEY = "materials-v4-date-hints"
DEFAULT_CONFIG = {
    "max_resources": 1000,
    "max_depth": 100,
    "retry_delay": 60,
    "max_bytes": 16 * 1024 * 1024,
}


def approved_origins(base_origin, config, *, attachments=False):
    """Optional legacy HTTP is an explicit same-host approval, never a wildcard."""
    values = {base_origin}
    if attachments and config.get("allow_http"):
        url = httpx.URL(base_origin)
        if url.scheme != "https" or url.port not in (None, 443):
            raise ValueError("Legacy HTTP approval requires a standard HTTPS source origin")
        values.add(f"http://{url.host}")
    return frozenset(values)


def toolkit_key():
    """Current extraction-code identity; source DAO/policy are not runtime versions."""
    return runtime_fingerprint()


@dataclass(frozen=True)
class ScanClaim:
    resource_id: str
    source_id: str
    url: str
    mode: str
    kind: str
    depth: int
    token: str
    started_at: datetime
    root_domain: str
    approved_origin: str
    config: dict


class PipelineStore:
    """Single-process transactions; the CLI additionally holds a DB-specific process lock."""

    def __init__(self, engine, storage):
        self.engine = engine
        self.blobs = BlobStore(storage)
        self.lock = RLock()

    @staticmethod
    def approved(session, source_id):
        source = session.get(Source, source_id)
        if source is None or source.status != RegistryStatus.ACTIVE:
            raise ValueError("Approve the registered source before onboarding")
        scope = session.get(OrganizationScope, source.publisher_organization_id)
        if scope is None or not DiscoveryScope(scope.root_domain).contains(source.base_url):
            raise ValueError("A matching approved organization root is required")
        return source, scope

    def start(self, source_id, config=None):
        values = DEFAULT_CONFIG | (config or {})
        if type(values.get("allow_http", False)) is not bool:
            raise ValueError("HTTP source approval must be an explicit boolean")
        if (
            type(values["max_resources"]) is not int
            or not 1 <= values["max_resources"] <= 10000
            or type(values["max_depth"]) is not int
            or not 1 <= values["max_depth"] <= 100
            or not 0 <= values["retry_delay"] <= 86400
            or not 1024 <= values["max_bytes"] <= 16 * 1024 * 1024
        ):
            raise ValueError("Invalid inventory limits")
        with self.lock, Session(self.engine) as session, session.begin():
            source, scope = self.approved(session, source_id)
            existing = session.get(SourcePipeline, source_id)
            if existing:
                return {"stage": existing.stage, "state": existing.state, "existing": True}
            if session.scalar(
                select(CrawlJob.resource_id)
                .join(Resource)
                .where(Resource.source_id == source_id, CrawlJob.state.in_(["ready", "running"]))
            ):
                raise ValueError("Finish the old POC queue before taking ownership with onboarding")
            pipeline = SourcePipeline(source_id=source_id, config=values, summary={})
            session.add(pipeline)
            session.add(
                SourceRecipeBinding(
                    source_id=source_id, workspace_id=digest({"source_id": source_id})
                )
            )
            session.flush()
            urls = [source.base_url] + [
                s.resource.normalized_url for s in source.seeds if s.status == RegistryStatus.ACTIVE
            ]
            for url in dict.fromkeys(urls):
                self._register(session, pipeline, source, scope.root_domain, url, 0)
            return {"stage": "inventory", "state": "ready", "existing": False}

    @staticmethod
    def _note(pipeline, code, url):
        summary = dict(pipeline.summary)
        issues = list(summary.get("inventory_issues", []))
        item = {"code": code, "url": url}
        if item not in issues:
            issues.append(item)
        summary["inventory_issues"] = issues[:100]
        if code in {"resource_limit", "depth_limit", "discovery_link_limit"}:
            summary["inventory_incomplete"] = True
            summary["inventory_blockers"] = sorted(
                set(summary.get("inventory_blockers", [])) | {code}
            )
        pipeline.summary = summary

    def _register(
        self, session, pipeline, source, root_domain, url, depth, attachment=False, link_kind=None
    ):
        try:
            url = normalize_url(url)
            in_root = DiscoveryScope(root_domain).contains(url)
            same_origin = origin(httpx.URL(url)) in approved_origins(
                origin(httpx.URL(source.base_url)), pipeline.config, attachments=attachment
            )
        except ValueError:
            return None
        if not in_root:
            return None
        target = session.scalar(select(Resource).where(Resource.normalized_url == url))
        if target is None:
            count = session.scalar(
                select(func.count())
                .select_from(ResourceScan)
                .join(Resource)
                .where(Resource.source_id == source.id)
            )
            if count >= pipeline.config["max_resources"]:
                self._note(pipeline, "resource_limit", url)
                return None
            target = Resource(
                original_url=url,
                source_id=source.id if same_origin else None,
                kind=ResourceKind.ATTACHMENT if attachment else ResourceKind.PAGE,
                review_status=ReviewStatus.APPROVED if same_origin else ReviewStatus.PENDING,
            )
            session.add(target)
            session.flush()
        elif (
            target.source_id is None
            and same_origin
            and target.review_status != ReviewStatus.REJECTED
        ):
            target.source_id = source.id
            target.review_status = ReviewStatus.APPROVED
        target.last_seen_at = utc_now()
        if not same_origin or target.source_id != source.id:
            self._note(pipeline, "unapproved_origin_candidate", url)
            return target
        if target.review_status == ReviewStatus.REJECTED:
            self._note(pipeline, "rejected_resource", url)
            return target
        if target.review_status != ReviewStatus.APPROVED:
            # Registration approval covers this origin, never a rejected resource.
            target.review_status = ReviewStatus.APPROVED
        purpose = link_kind or ("attachment" if attachment else "page")
        if purpose in {"asset", "form"}:
            # Structural URL references are retained, but are not page acquisitions.
            # Never demote a previously acquired document just because it is also an asset.
            acquired_document = session.scalar(
                select(MaterialDocument.id)
                .join(ResourceVersion)
                .where(
                    ResourceVersion.resource_id == target.id,
                    MaterialDocument.format.in_(
                        ["html", "xml", "pdf", "doc", "docx", "xls", "xlsx"]
                    ),
                    MaterialDocument.status.in_(["ok", "partial"]),
                )
            )
            if acquired_document is None:
                target.kind = ResourceKind.UNKNOWN
                self._note(pipeline, "non_page_" + purpose, url)
                return target
        elif attachment:
            target.kind = ResourceKind.ATTACHMENT
        elif target.kind == ResourceKind.UNKNOWN:
            target.kind = ResourceKind.PAGE
        existing_scan = session.get(ResourceScan, target.id)
        if (
            existing_scan
            and existing_scan.last_error == "depth_limit"
            and depth <= pipeline.config["max_depth"]
        ):
            existing_scan.depth = min(existing_scan.depth, depth)
            existing_scan.state, existing_scan.available_at = "ready", utc_now()
            existing_scan.last_error = None
        if existing_scan is None:
            count = session.scalar(
                select(func.count())
                .select_from(ResourceScan)
                .join(Resource)
                .where(Resource.source_id == source.id)
            )
            if count >= pipeline.config["max_resources"]:
                self._note(pipeline, "resource_limit", url)
                return target
            limited = depth > pipeline.config["max_depth"]
            session.add(
                ResourceScan(
                    resource_id=target.id,
                    depth=depth,
                    state="review" if limited else "ready",
                    last_error="depth_limit" if limited else None,
                )
            )
            if limited:
                self._note(pipeline, "depth_limit", url)
        return target

    def claim(self, source_id, excluded, timeout=45):
        with self.lock, Session(self.engine) as session, session.begin():
            source, scope = self.approved(session, source_id)
            pipeline = session.get(SourcePipeline, source_id)
            if pipeline is None or pipeline.stage not in {"inventory", "active"}:
                return None
            stamp = utc_now()
            due = or_(
                (ResourceScan.state == "ready") & (ResourceScan.available_at <= stamp),
                (ResourceScan.state == "running") & (ResourceScan.lease_expires_at <= stamp),
            )
            statement = (
                select(ResourceScan)
                .join(Resource)
                .where(
                    Resource.source_id == source_id,
                    Resource.review_status == ReviewStatus.APPROVED,
                    Resource.kind != ResourceKind.UNKNOWN,
                    ResourceScan.resource_id.not_in(excluded),
                    due,
                )
                .order_by(
                    (Resource.kind == ResourceKind.ATTACHMENT).desc(),
                    (ResourceScan.mode == "dynamic").desc(),
                    ResourceScan.available_at,
                )
            )
            for scan in session.scalars(statement):
                resource = session.get(Resource, scan.resource_id)
                if not DiscoveryScope(scope.root_domain).contains(
                    resource.normalized_url
                ) or origin(httpx.URL(resource.normalized_url)) not in approved_origins(
                    origin(httpx.URL(source.base_url)),
                    pipeline.config,
                    attachments=resource.kind == ResourceKind.ATTACHMENT,
                ):
                    scan.state, scan.last_error = "review", "outside_scope"
                    continue
                token = uuid4().hex
                updated = session.execute(
                    update(ResourceScan)
                    .where(
                        ResourceScan.resource_id == resource.id,
                        due,
                    )
                    .values(
                        state="running",
                        lease_token=token,
                        lease_expires_at=stamp + timedelta(seconds=timeout + 120),
                        attempts=ResourceScan.attempts + 1,
                    )
                )
                if updated.rowcount:
                    return ScanClaim(
                        resource.id,
                        source_id,
                        resource.normalized_url,
                        scan.mode,
                        resource.kind.value,
                        scan.depth,
                        token,
                        stamp,
                        scope.root_domain,
                        origin(httpx.URL(source.base_url)),
                        dict(pipeline.config),
                    )
        return None

    @staticmethod
    def _owned(session, claim):
        scan = session.get(ResourceScan, claim.resource_id)
        if scan is not None and scan.state == "running" and scan.lease_token == claim.token:
            return scan
        return None

    @staticmethod
    def _release(scan, claim, error=None, terminal=False):
        scan.lease_token = scan.lease_expires_at = None
        scan.last_error = error
        if error is None:
            scan.state = "done"
        elif not terminal and claim.mode == "static" and claim.kind != "attachment":
            scan.mode, scan.state = "dynamic", "ready"
            scan.available_at = utc_now() + timedelta(seconds=claim.config["retry_delay"])
        else:
            scan.state = "review"

    def fail(self, claim, code, *, terminal=False, http_status=None, final_url=None):
        with self.lock, Session(self.engine) as session, session.begin():
            scan = self._owned(session, claim)
            if scan is None:
                return "stale"
            attempt = FetchAttempt(
                resource_id=claim.resource_id,
                started_at=claim.started_at,
                finished_at=utc_now(),
                outcome=FetchOutcome.FAILED,
                error_code=code[:128],
                http_status=http_status,
                final_url=final_url,
            )
            session.add(attempt)
            session.flush()
            scan.last_attempt_id = attempt.id
            session.get(Resource, claim.resource_id).consecutive_failures += 1
            self._release(scan, claim, code, terminal)
            return scan.state

    def save(self, claim, response, document):
        """Called off the event loop; blob writes precede the short database transaction."""
        body_ref = self.blobs.put(response.body)
        original = (
            self.blobs.put(response.response_body) if response.response_body is not None else None
        )
        document_ref = self.blobs.put(json_bytes(document))
        with self.lock, Session(self.engine) as session, session.begin():
            scan = self._owned(session, claim)
            if scan is None:
                return "stale"
            source, scope = self.approved(session, claim.source_id)
            pipeline = session.get(SourcePipeline, claim.source_id)
            resource = session.get(Resource, claim.resource_id)
            version = session.scalar(
                select(ResourceVersion).where(
                    ResourceVersion.resource_id == resource.id,
                    ResourceVersion.content_sha256 == body_ref,
                )
            )
            if version is None:
                version = ResourceVersion(
                    resource_id=resource.id,
                    content_sha256=body_ref,
                    body_ref=body_ref,
                    byte_length=len(response.body),
                    media_type=response.media_type,
                    captured_at=response.captured_at,
                )
                session.add(version)
                session.flush()
            doc = session.scalar(
                select(MaterialDocument).where(
                    MaterialDocument.version_id == version.id,
                    MaterialDocument.parser_key == MATERIAL_PARSER_KEY,
                    MaterialDocument.body_ref == document_ref,
                )
            )
            if doc is None:
                doc = MaterialDocument(
                    version_id=version.id,
                    parser_key=MATERIAL_PARSER_KEY,
                    format=document["format"],
                    status=document["status"],
                    body_ref=document_ref,
                )
                session.add(doc)
                session.flush()
            attempt = FetchAttempt(
                resource_id=resource.id,
                version_id=version.id,
                started_at=claim.started_at,
                finished_at=utc_now(),
                outcome=FetchOutcome.DOWNLOADED,
                http_status=response.http_status,
                final_url=response.final_url,
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
            )
            session.add(attempt)
            session.flush()
            if response.html_kind:
                session.add(
                    Capture(
                        attempt_id=attempt.id,
                        version_id=version.id,
                        mode=claim.mode,
                        html_kind=response.html_kind,
                        response_body_ref=original,
                    )
                )
            scan.last_version_id, scan.last_document_id, scan.last_attempt_id = (
                version.id,
                doc.id,
                attempt.id,
            )
            scan.bundle_id = None  # Reattach only after all current material dependencies settle.
            scan.last_success_at = utc_now()
            resource.last_seen_at, resource.consecutive_failures = utc_now(), 0
            if resource.kind != ResourceKind.ATTACHMENT:
                self.discover_document(
                    session, pipeline, source, scope.root_domain, version.id, claim.depth, document
                )
            # An empty HTML shell gets one later dynamic attempt, not an inline retry.
            useful = bool(
                document.get("blocks")
                or document.get("tables")
                or any(
                    link.get("kind", "page") in {"page", "attachment"}
                    for link in document.get("links", [])
                )
            )
            if document.get("truncated"):
                # A document reader limit is an audit failure, not an unfinished URL frontier.
                self._note(pipeline, "document_limit", claim.url)
                if document["format"] in {"html", "xml"} and any(
                    item.get("code") in {"LINK_LIMIT", "LINK_TRUNCATED", "OUTPUT_LIMIT"}
                    for item in document.get("errors", [])
                ):
                    self._note(pipeline, "discovery_link_limit", claim.url)
            if document["status"] not in {"ok", "partial"}:
                self._release(
                    scan, claim, "material_parse_failed", terminal=claim.kind == "attachment"
                )
            elif not useful and document["format"] == "html":
                self._release(scan, claim, "no_valuable_content")
            else:
                self._release(scan, claim)
            policy = session.get(ScanPolicy, resource.id)
            if policy and scan.state == "done":
                policy.next_scan_at = self.next_scan_at(scan.last_success_at, policy.cadence)
            return scan.state

    def discover_document(
        self, session, pipeline, source, root_domain, version_id, depth, document
    ):
        """Register syntactic references; this does not refresh any HTTP observation."""
        if document["format"] in {"html", "xml"}:
            for link in document.get("links", []):
                target = self._register(
                    session,
                    pipeline,
                    source,
                    root_domain,
                    link["url"],
                    depth + 1,
                    link.get("is_attachment", False),
                    link.get("kind"),
                )
                if target is None:
                    continue
                method = (
                    DiscoveryMethod.SITEMAP if document["format"] == "xml" else DiscoveryMethod.LINK
                )
                prior = session.scalar(
                    select(Discovery.id).where(
                        Discovery.parent_version_id == version_id,
                        Discovery.target_resource_id == target.id,
                        Discovery.method == method,
                        Discovery.raw_url == link["raw_url"],
                    )
                )
                if prior is None:
                    session.add(
                        Discovery(
                            parent_version_id=version_id,
                            target_resource_id=target.id,
                            method=method,
                            raw_url=link["raw_url"],
                            link_text=link.get("label"),
                        )
                    )

    @staticmethod
    def next_scan_at(stamp, cadence):
        return stamp + timedelta(days=1 if cadence == "daily" else 7) if cadence != "none" else None

    def counts(self, source_id):
        with self.lock, Session(self.engine) as session:
            values = session.execute(
                select(ResourceScan.state, func.count())
                .join(Resource)
                .where(Resource.source_id == source_id, Resource.kind != ResourceKind.UNKNOWN)
                .group_by(ResourceScan.state)
            ).all()
            return dict(values)

    def status(self, source_id):
        with self.lock, Session(self.engine) as session:
            row = session.get(SourcePipeline, source_id)
            if row is None:
                raise ValueError("Source onboarding has not been started")
            binding = session.get(SourceRecipeBinding, source_id)
            result = {
                "source_id": source_id,
                "stage": row.stage,
                "state": row.state,
                "active_recipe_id": binding.active_recipe_id if binding else None,
                "candidate_recipe_id": binding.candidate_recipe_id if binding else None,
                "binding": {
                    key: getattr(binding, key)
                    for key in (
                        "workspace_id",
                        "active_revision_id",
                        "candidate_revision_id",
                        "generation",
                    )
                }
                if binding
                else None,
                "config": row.config,
                "summary": row.summary,
            }
            from .lifecycle import incremental_json

            result["incremental"] = incremental_json(session, source_id)
            from youju.models import RepairRequest

            from .auto_repair import request_json

            result["repairs"] = [
                request_json(request)
                for request in session.scalars(
                    select(RepairRequest)
                    .where(RepairRequest.source_id == source_id)
                    .order_by(RepairRequest.created_at.desc(), RepairRequest.id.desc())
                    .limit(20)
                )
            ]

        with self.lock, Session(self.engine) as session:
            non_pages = session.scalar(
                select(func.count())
                .select_from(Resource)
                .where(
                    Resource.source_id == source_id,
                    Resource.kind == ResourceKind.UNKNOWN,
                )
            )
        return result | {"acquisition": self.counts(source_id), "non_page_resources": non_pages}

    def finish_inventory(self, source_id):
        with self.lock, Session(self.engine) as session, session.begin():
            pipeline = session.get(SourcePipeline, source_id)
            if pipeline.stage != "inventory":
                return False
            pending = session.scalar(
                select(ResourceScan.resource_id)
                .join(Resource)
                .where(
                    Resource.source_id == source_id,
                    Resource.kind != ResourceKind.UNKNOWN,
                    ResourceScan.state.in_(["ready", "running"]),
                )
            )
            if pending:
                return False
            if pipeline.summary.get("inventory_incomplete"):
                blockers = set(pipeline.summary.get("inventory_blockers", []))
                if session.scalar(
                    select(ResourceScan.resource_id)
                    .join(Resource)
                    .where(
                        Resource.source_id == source_id,
                        ResourceScan.last_error == "depth_limit",
                        ResourceScan.state == "review",
                    )
                    .limit(1)
                ):
                    blockers.add("depth_limit")
                code = (
                    "INVENTORY_DEPTH_LIMIT"
                    if blockers == {"depth_limit"}
                    else "INVENTORY_INCOMPLETE"
                )
                pipeline.summary = dict(
                    pipeline.summary, inventory_blockers=sorted(blockers), error={"code": code}
                )
                pipeline.state, pipeline.updated_at = "blocked", utc_now()
                return False
            pipeline.stage, pipeline.state, pipeline.updated_at = (
                "classification",
                "ready",
                utc_now(),
            )
            return True

    def build_bundles(self, source_id):
        """Freeze page + current attachment dependency versions after every terminal acquisition."""
        built = []
        with self.lock, Session(self.engine) as session, session.begin():
            source, scope = self.approved(session, source_id)
            scans = session.scalars(
                select(ResourceScan)
                .join(Resource)
                .where(
                    Resource.source_id == source_id,
                    Resource.kind.not_in([ResourceKind.ATTACHMENT, ResourceKind.UNKNOWN]),
                    ResourceScan.last_document_id.is_not(None),
                    ResourceScan.state.in_(["done", "review"]),
                )
            ).all()
            for scan in scans:
                resource = session.get(Resource, scan.resource_id)
                page = self._document(session, scan, resource)
                if page["format"] not in {"html", "xml"}:
                    continue
                attachments, dependencies, pending = [], [], False
                material_bytes = len(json_bytes(page))
                bundle_limit = 6 * 1024 * 1024
                seen_attachments = set()
                for link in page.get("links", []):
                    if not link.get("is_attachment"):
                        continue
                    url = link["url"]
                    if url in seen_attachments:
                        continue
                    seen_attachments.add(url)
                    target = session.scalar(select(Resource).where(Resource.normalized_url == url))
                    child = session.get(ResourceScan, target.id) if target is not None else None
                    if child and child.state in {"ready", "running"}:
                        pending = True
                        break
                    if not DiscoveryScope(scope.root_domain).contains(url) or origin(
                        httpx.URL(url)
                    ) not in approved_origins(
                        origin(httpx.URL(source.base_url)),
                        session.get(SourcePipeline, source_id).config,
                        attachments=True,
                    ):
                        material = self._missing(url, "attachment_outside_approved_scope")
                    elif child and child.last_document_id and child.state == "done":
                        if material_bytes >= bundle_limit:
                            material = self._missing(url, "bundle_material_limit")
                        else:
                            material = self._document(session, child, target)
                            size = len(json_bytes(material))
                            if material_bytes + size > bundle_limit:
                                material = self._missing(url, "bundle_material_limit")
                                material_bytes = bundle_limit
                            else:
                                material_bytes += size
                    else:
                        material = self._missing(
                            url, child.last_error if child else "attachment_unavailable"
                        )
                    if target:
                        material["resource_id"] = target.id
                    material["link_label"] = link.get("label", "")
                    attachments.append(material)
                    dependencies.append(
                        {
                            "url": url,
                            "document_id": child.last_document_id
                            if child and child.state == "done"
                            else None,
                            "error": material.get("errors"),
                        }
                    )
                if pending:
                    continue
                if scan.state != "done":
                    page = dict(
                        page,
                        status="failed",
                        errors=page.get("errors", [])
                        + [
                            {
                                "code": scan.last_error or "page_failed",
                                "message": "Latest acquisition needs review",
                            }
                        ],
                    )
                manifest = {
                    "page_document_id": scan.last_document_id,
                    "page_state": scan.state,
                    "page_error": scan.last_error,
                    "attachments": dependencies,
                }
                bundle_digest = digest(manifest)
                bundle = session.scalar(
                    select(ResourceBundle).where(
                        ResourceBundle.resource_id == resource.id,
                        ResourceBundle.digest == bundle_digest,
                    )
                )
                if bundle is None:
                    bundle_id = uuid4().hex
                    payload = {
                        "id": bundle_id,
                        "source_id": source_id,
                        "resource_id": resource.id,
                        "url": resource.normalized_url,
                        "page": page,
                        "attachments": attachments,
                        "captured_at": scan.last_success_at.isoformat(),
                        "status": "complete"
                        if all(d["status"] == "ok" for d in [page, *attachments])
                        else "partial",
                        "errors": [e for d in [page, *attachments] for e in d.get("errors", [])],
                    }
                    bundle = ResourceBundle(
                        id=bundle_id,
                        resource_id=resource.id,
                        page_version_id=scan.last_version_id,
                        digest=bundle_digest,
                        body_ref=self.blobs.put(json_bytes(payload)),
                        status=payload["status"],
                        dependencies=dependencies,
                    )
                    session.add(bundle)
                    session.flush()
                    built.append(bundle.id)
                scan.bundle_id = bundle.id
        return built

    @staticmethod
    def _missing(url, code):
        suffix = urlsplit(url).path.rsplit(".", 1)[-1].lower()
        return {
            "url": url,
            "format": suffix if suffix in {"pdf", "docx", "doc", "xlsx", "xls"} else "unknown",
            "status": "failed",
            "blocks": [],
            "tables": [],
            "links": [],
            "truncated": False,
            "errors": [
                {
                    "code": code or "attachment_unavailable",
                    "message": "Material not successfully acquired",
                }
            ],
        }

    def _document(self, session, scan, resource):
        doc = session.get(MaterialDocument, scan.last_document_id)
        version = session.get(ResourceVersion, doc.version_id)
        value = json.loads(self.blobs.read(doc.body_ref))
        return value | {
            "url": resource.normalized_url,
            "resource_id": resource.id,
            "version_id": version.id,
            "sha256": version.content_sha256,
        }

    def bundles(self, source_id, *, classifier_key=None, unassessed=False, summaries=False):
        with self.lock, Session(self.engine) as session:
            records = session.scalars(
                select(ResourceBundle)
                .join(ResourceScan, ResourceScan.bundle_id == ResourceBundle.id)
                .join(Resource, ResourceScan.resource_id == Resource.id)
                .where(Resource.source_id == source_id, ResourceScan.state.in_(["done", "review"]))
            ).all()
            result = []
            for record in records:
                query = select(PageAssessment).where(PageAssessment.bundle_id == record.id)
                if classifier_key:
                    query = query.where(PageAssessment.classifier_key == classifier_key)
                assessment = session.scalar(query.order_by(PageAssessment.created_at.desc()))
                if unassessed and assessment is not None:
                    continue
                if summaries:
                    page_doc = session.scalar(
                        select(MaterialDocument)
                        .where(
                            MaterialDocument.version_id == record.page_version_id,
                        )
                        .order_by(MaterialDocument.created_at.desc())
                    )
                    attachment_docs = [
                        session.get(MaterialDocument, dependency["document_id"])
                        if dependency.get("document_id")
                        else None
                        for dependency in record.dependencies
                    ]
                    bundle = {
                        "id": record.id,
                        "resource_id": record.resource_id,
                        "url": session.get(Resource, record.resource_id).normalized_url,
                        "source_id": source_id,
                        "status": record.status,
                        "page": {"format": page_doc.format, "status": page_doc.status},
                        "attachments": [
                            {
                                "format": doc.format if doc else "unknown",
                                "status": doc.status if doc else "failed",
                            }
                            for doc in attachment_docs
                        ],
                    }
                else:
                    bundle = json.loads(self.blobs.read(record.body_ref))
                if assessment:
                    bundle["assessment"] = assessment.result
                if not summaries:
                    from .temporal_context import attach_context

                    bundle = attach_context(self, session, bundle)
                else:
                    from .temporal_context import associated_event_reviews

                    related = associated_event_reviews(session, record.resource_id, record.id)
                    if related:
                        bundle["associated_event_reviews"] = related
                result.append(bundle)
            return result

    def load_bundle(self, bundle_id, *, classifier_key=None):
        with self.lock, Session(self.engine) as session:
            record = session.get(ResourceBundle, bundle_id)
            if record is None:
                raise ValueError("Unknown resource bundle")
            value = json.loads(self.blobs.read(record.body_ref))
            query = select(PageAssessment).where(PageAssessment.bundle_id == bundle_id)
            if classifier_key:
                query = query.where(PageAssessment.classifier_key == classifier_key)
            assessment = session.scalar(query.order_by(PageAssessment.created_at.desc()))
            if assessment:
                value["assessment"] = assessment.result
            from .temporal_context import attach_context

            return attach_context(self, session, value)

    def assess(self, bundle, result, classifier_key):
        result = scheduled(result)
        with self.lock, Session(self.engine) as session, session.begin():
            scan = session.get(ResourceScan, bundle["resource_id"])
            if scan.bundle_id != bundle["id"]:
                return False
            row = session.scalar(
                select(PageAssessment).where(
                    PageAssessment.bundle_id == bundle["id"],
                    PageAssessment.classifier_key == classifier_key,
                )
            )
            if row is None:
                row = PageAssessment(
                    bundle_id=bundle["id"], classifier_key=classifier_key, result=result
                )
                session.add(row)
                session.flush()
            cadence = result["revisit"]
            if result.get("requires_review") or bundle["status"] != "complete":
                cadence = "weekly" if cadence == "none" else cadence
            policy = session.get(ScanPolicy, bundle["resource_id"])
            if policy is None:
                policy = ScanPolicy(
                    resource_id=bundle["resource_id"],
                    assessment_id=row.id,
                    cadence=cadence,
                    reason=result["reason"],
                )
                session.add(policy)
            policy.assessment_id, policy.cadence, policy.reason = row.id, cadence, result["reason"]
            policy.next_scan_at = self.next_scan_at(scan.last_success_at or utc_now(), cadence)
            return True

    def queue_due(self, source_id, stamp=None):
        stamp = stamp or utc_now()
        count = 0
        with self.lock, Session(self.engine) as session, session.begin():
            self.approved(session, source_id)
            pipeline = session.get(SourcePipeline, source_id)
            binding = session.get(SourceRecipeBinding, source_id)
            if (
                pipeline.stage != "active"
                or binding is None
                or binding.active_recipe_id is None
                or pipeline.state != "ready"
            ):
                raise ValueError("Only activated sources may run incremental scans")
            policies = session.scalars(
                select(ScanPolicy)
                .join(Resource)
                .where(Resource.source_id == source_id, ScanPolicy.next_scan_at <= stamp)
            ).all()
            for policy in policies:
                scan = session.get(ResourceScan, policy.resource_id)
                if scan.state != "done":
                    continue  # Retry/review state is not a periodic scheduling decision.
                scan.state, scan.available_at = "ready", stamp
                count += 1
                # Recheck attachment URLs even if the parent HTML has not changed.
                for edge in session.scalars(
                    select(Discovery).where(Discovery.parent_version_id == scan.last_version_id)
                ):
                    child_resource = session.get(Resource, edge.target_resource_id)
                    child = session.get(ResourceScan, edge.target_resource_id)
                    if (
                        child
                        and child_resource.kind == ResourceKind.ATTACHMENT
                        and child_resource.source_id == source_id
                        and child.state == "done"
                    ):
                        child.state, child.available_at = "ready", stamp
                        count += 1
        return count

    def reanalyze(self, source_id):
        """Restart analysis of saved bundles, without retrying or refreshing acquisition.

        Prior summaries form content-addressed linked snapshots, not nested copies in
        the current status. Recipes, audits and the source-wide LLM budget remain intact.
        This command never reclaims an operation lease, including an expired one.
        """
        with self.lock, Session(self.engine) as session, session.begin():
            self.approved(session, source_id)
            pipeline = session.get(SourcePipeline, source_id)
            if pipeline is None:
                raise ValueError("Source onboarding has not been started")
            if (
                pipeline.state == "running"
                or pipeline.lease_token is not None
                or pipeline.lease_expires_at is not None
            ):
                raise ValueError("A source operation is still leased; reanalysis cannot reclaim it")
            pending = session.scalar(
                select(ResourceScan.resource_id)
                .join(Resource)
                .where(
                    Resource.source_id == source_id,
                    Resource.kind != ResourceKind.UNKNOWN,
                    ResourceScan.state.in_(["ready", "running"]),
                )
            )
            if pending:
                raise ValueError("Finish pending acquisition work before reanalyzing saved bundles")
            saved = session.scalar(
                select(ResourceBundle.id)
                .join(ResourceScan, ResourceScan.bundle_id == ResourceBundle.id)
                .join(Resource, ResourceScan.resource_id == Resource.id)
                .where(
                    Resource.source_id == source_id,
                    Resource.kind != ResourceKind.UNKNOWN,
                    ResourceScan.state.in_(["done", "review"]),
                )
            )
            if saved is None:
                raise ValueError("No saved resource bundles are available for reanalysis")

            stamp = utc_now()
            config = dict(pipeline.config)
            from .recipe_versions import next_generation

            binding = session.get(SourceRecipeBinding, source_id)
            generation = next_generation(session, source_id)
            summary = dict(pipeline.summary)
            previous_summary_ref = self.blobs.put(json_bytes(summary))
            # Keep inventory failures and durable classification reservations visible;
            # an old passing audit must not describe the newly requested generation.
            for key in (
                "audit",
                "audit_id",
                "authoring",
                "authoring_job",
                "classification",
                "error",
            ):
                summary.pop(key, None)
            summary["reanalysis"] = {
                "generation": generation,
                "requested_at": stamp.isoformat(),
                "previous_stage": pipeline.stage,
                "previous_state": pipeline.state,
                "previous_active_recipe_id": binding.active_recipe_id,
                "previous_candidate_recipe_id": binding.candidate_recipe_id,
                "previous_summary_ref": previous_summary_ref,
            }
            summary["candidate_generation"] = generation
            pipeline.config, pipeline.summary = config, summary
            binding.candidate_recipe_id = binding.candidate_revision_id = None
            pipeline.stage, pipeline.state, pipeline.updated_at = "classification", "ready", stamp

    def resume(self, source_id, *, max_resources=None, max_depth=None, allow_http=None):
        with self.lock, Session(self.engine) as session, session.begin():
            pipeline = session.get(SourcePipeline, source_id)
            if pipeline is None:
                raise ValueError("Unknown onboarding")
            if pipeline.state == "running" and pipeline.summary.get("authoring_job"):
                raise ValueError(
                    "A source author job is still leased; cancel/recover it and collect "
                    "its outcome with source-step before resuming"
                )
            if (
                pipeline.state == "running"
                and pipeline.lease_expires_at
                and pipeline.lease_expires_at > utc_now()
            ):
                raise ValueError("A source operation is still leased")
            config = dict(pipeline.config)
            if allow_http is not None:
                if type(allow_http) is not bool:
                    raise ValueError("HTTP source approval must be an explicit boolean")
                config["allow_http"] = allow_http
                if allow_http:
                    pipeline.stage = "inventory"
                config["origin_approval"] = (
                    "Operator approved anonymous same-host HTTP attachments."
                )
            if max_resources is not None:
                if (
                    type(max_resources) is not int
                    or not config["max_resources"] <= max_resources <= 10000
                ):
                    raise ValueError("Resource budget can only increase, up to 10000")
                config["max_resources"] = max_resources
            if max_depth is not None:
                if type(max_depth) is not int or not config["max_depth"] <= max_depth <= 100:
                    raise ValueError("Depth budget can only increase, up to 100")
                config["max_depth"] = max_depth
            from .recipe_versions import next_generation

            pipeline.summary = dict(
                pipeline.summary, candidate_generation=next_generation(session, source_id)
            )
            if pipeline.stage == "active":
                pipeline.summary = dict(
                    pipeline.summary,
                    incremental_generation=pipeline.summary["candidate_generation"],
                )
            pipeline.config, pipeline.state = config, "ready"
            pipeline.lease_token = pipeline.lease_expires_at = None
            if pipeline.summary.get("authoring_job"):
                summary = dict(pipeline.summary)
                summary["previous_authoring_job"] = summary.pop("authoring_job")
                pipeline.summary = summary
            if pipeline.summary.get("inventory_incomplete"):
                pipeline.stage = "inventory"
            failed_scans = session.scalars(
                select(ResourceScan)
                .join(Resource)
                .where(
                    Resource.source_id == source_id,
                    ResourceScan.state == "review",
                )
            ).all()
            for failed in failed_scans:
                if failed.last_error == "depth_limit" and failed.depth > config["max_depth"]:
                    continue  # Retrying cannot authorize crawling past the selected depth cap.
                failed.state, failed.available_at = "ready", utc_now()
            if failed_scans and pipeline.stage != "active":
                pipeline.stage = "inventory"
            if pipeline.stage == "inventory":
                # Replaying saved pages discovers links previously excluded by a budget.
                scans = session.scalars(
                    select(ResourceScan).join(Resource).where(Resource.source_id == source_id)
                ).all()
                summary = dict(pipeline.summary)
                summary.pop("inventory_incomplete", None)
                summary.pop("inventory_blockers", None)
                summary["inventory_issues"] = []
                pipeline.summary = summary
                source, scope = self.approved(session, source_id)
                for scan in scans:
                    if not scan.last_document_id:
                        continue
                    material = session.get(MaterialDocument, scan.last_document_id)
                    document = json.loads(self.blobs.read(material.body_ref))
                    if session.get(Resource, scan.resource_id).kind == ResourceKind.ATTACHMENT:
                        continue
                    self.discover_document(
                        session,
                        pipeline,
                        source,
                        scope.root_domain,
                        material.version_id,
                        scan.depth,
                        document,
                    )
            for failed in failed_scans:
                if failed.last_error == "depth_limit" and failed.state == "review":
                    self._note(
                        pipeline,
                        "depth_limit",
                        session.get(Resource, failed.resource_id).normalized_url,
                    )
            summary = dict(pipeline.summary)
            if "error" in summary:
                summary["previous_error"] = summary.pop("error")
            pipeline.summary, pipeline.updated_at = summary, utc_now()
