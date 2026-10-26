"""Replay saved originals through a newer reader without pretending to fetch them again."""

import asyncio

from sqlalchemy import select
from sqlalchemy.orm import Session

from youju.models import (
    FetchAttempt,
    FetchOutcome,
    MaterialDocument,
    Resource,
    ResourceKind,
    ResourceScan,
    ResourceVersion,
    SourcePipeline,
)

from .acquisition import observe_http_date, prepare_document
from .sandbox import Sandbox
from .store import MATERIAL_PARSER_KEY, json_bytes


async def reparse_saved(store, source_id, *, limit=500, sandbox=None, force=False, formats=()):
    """Only successful saved observations are eligible; current network failures stay failures."""
    sandbox = sandbox or Sandbox()
    if not 1 <= limit <= 10000:
        raise ValueError("Reparse limit must be between 1 and 10000")
    with store.lock, Session(store.engine) as session:
        store.approved(session, source_id)
        if session.scalar(
            select(ResourceScan.resource_id)
            .join(Resource)
            .where(
                Resource.source_id == source_id,
                ResourceScan.state == "running",
            )
        ):
            raise ValueError("Finish the acquisition pass before replaying saved originals")
        statement = (
            select(Resource, ResourceScan, ResourceVersion, FetchAttempt)
            .join(ResourceScan, ResourceScan.resource_id == Resource.id)
            .join(ResourceVersion, ResourceVersion.id == ResourceScan.last_version_id)
            .join(FetchAttempt, FetchAttempt.id == ResourceScan.last_attempt_id)
            .where(
                Resource.source_id == source_id,
                ResourceScan.state.in_(["done", "review"]),
                FetchAttempt.outcome == FetchOutcome.DOWNLOADED,
                FetchAttempt.version_id == ResourceVersion.id,
            )
        )
        work = []
        for resource, scan, version, attempt in session.execute(statement):
            previous = session.get(MaterialDocument, scan.last_document_id)
            if formats and (previous is None or previous.format not in formats):
                continue
            if previous and previous.parser_key == MATERIAL_PARSER_KEY and not force:
                continue
            work.append(
                {
                    "resource_id": resource.id,
                    "version_id": version.id,
                    "body_ref": version.body_ref,
                    "media_type": version.media_type,
                    "url": attempt.final_url or resource.normalized_url,
                    "kind": resource.kind.value,
                    "last_modified": attempt.last_modified,
                }
            )
            if len(work) >= limit:
                break
    semaphore = asyncio.Semaphore(2)

    async def one(item):
        async with semaphore:
            body = await asyncio.to_thread(store.blobs.read, item["body_ref"])
            document = await sandbox.parse(
                body,
                media_type=item["media_type"] or "",
                url=item["url"],
                material_key=f"{item['resource_id']}:{item['body_ref']}",
            )
            document = prepare_document(document, body, item["kind"])
            observe_http_date(document, item["last_modified"])
            return await asyncio.to_thread(_save, store, source_id, item, document)

    results = await asyncio.gather(*(one(item) for item in work))
    await asyncio.to_thread(store.build_bundles, source_id)
    return {
        "reparsed": sum(results),
        "selected": len(work),
        "parser_key": MATERIAL_PARSER_KEY,
        "network_requests": 0,
    }


def _save(store, source_id, item, document):
    reference = store.blobs.put(json_bytes(document))
    with store.lock, Session(store.engine) as session, session.begin():
        source, scope = store.approved(session, source_id)
        scan = session.get(ResourceScan, item["resource_id"])
        if scan.last_version_id != item["version_id"] or scan.state == "running":
            return 0
        attempt = session.get(FetchAttempt, scan.last_attempt_id)
        if attempt.outcome != FetchOutcome.DOWNLOADED or attempt.version_id != item["version_id"]:
            return 0
        row = session.scalar(
            select(MaterialDocument).where(
                MaterialDocument.version_id == item["version_id"],
                MaterialDocument.parser_key == MATERIAL_PARSER_KEY,
                MaterialDocument.body_ref == reference,
            )
        )
        if row is None:
            row = MaterialDocument(
                version_id=item["version_id"],
                parser_key=MATERIAL_PARSER_KEY,
                body_ref=reference,
                format=document["format"],
                status=document["status"],
            )
            session.add(row)
            session.flush()
        scan.last_document_id, scan.bundle_id = row.id, None
        resource = session.get(Resource, item["resource_id"])
        if resource.kind == ResourceKind.UNKNOWN and document["format"] == "html":
            resource.kind = ResourceKind.PAGE
        pipeline = session.get(SourcePipeline, source_id)
        if resource.kind != ResourceKind.ATTACHMENT:
            store.discover_document(
                session,
                pipeline,
                source,
                scope.root_domain,
                item["version_id"],
                scan.depth,
                document,
            )
        # Reparse can fix a reader failure, never a failed HTTP observation.
        if scan.state == "review" and scan.last_error in {
            "material_parse_failed",
            "no_valuable_content",
        }:
            if document["status"] in {"ok", "partial"} and any(
                document.get(key) for key in ("blocks", "tables", "links")
            ):
                scan.state, scan.last_error = "done", None
        pipeline.summary = dict(pipeline.summary, reader_version=MATERIAL_PARSER_KEY)
    return 1
