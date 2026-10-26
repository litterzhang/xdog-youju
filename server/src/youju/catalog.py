"""Insert-only source registry import. Replays preserve review state and crawl history."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from youju.models import (
    Organization,
    OrganizationType,
    Region,
    Resource,
    ResourceKind,
    Seed,
    SeedKind,
    Source,
    normalize_url,
)


class CatalogError(ValueError):
    """Invalid catalog data or a conflict requiring explicit human review."""


def _record(value, required: dict, optional: dict | None = None) -> dict:
    fields = required | (optional or {})
    if not isinstance(value, dict):
        raise CatalogError("Each catalog record must be an object")
    if missing := required.keys() - value.keys():
        raise CatalogError(f"Missing fields: {', '.join(sorted(missing))}")
    if unknown := value.keys() - fields.keys():
        raise CatalogError(f"Unsupported fields: {', '.join(sorted(unknown))}")
    for key, item in value.items():
        if type(item) is not fields[key]:
            raise CatalogError(f"{key} must be a {fields[key].__name__}")
        if isinstance(item, str) and not item.strip():
            raise CatalogError(f"{key} must not be blank")
    return value.copy()


def _insert_or_match(session, model, identity, values, created):
    record = session.get(model, identity)
    if record is not None:
        for field, value in values.items():
            if getattr(record, field) != value:
                raise CatalogError(f"Conflicting {model.__name__} {identity!r}: {field}")
        return record, False
    record = model(**values)
    session.add(record)
    session.flush()
    created[model.__tablename__] += 1
    return record, True


def import_catalog(session: Session, data: dict) -> dict[str, int]:
    """Import within a caller-owned transaction; never commit, fetch, approve or overwrite."""
    data = _record(data, {"regions": list, "organizations": list, "sources": list})
    created = dict.fromkeys(("regions", "organizations", "sources", "resources", "seeds"), 0)
    seen = set()

    def register(model, identity, values):
        key = (model, identity)
        if key in seen:
            raise CatalogError(f"Duplicate {model.__name__} identifier: {identity!r}")
        seen.add(key)
        return _insert_or_match(session, model, identity, values, created)

    for item in data["regions"]:
        item = _record(item, {"code": str, "name": str}, {"parent_code": str})
        item.setdefault("parent_code", None)
        register(Region, item["code"], item)

    for item in data["organizations"]:
        item = _record(
            item,
            {"id": str, "name": str},
            {
                "short_name": str,
                "organization_type": str,
                "parent_id": str,
                "location_region_code": str,
            },
        )
        item["organization_type"] = OrganizationType(item.get("organization_type", "unknown"))
        for field in ("short_name", "parent_id", "location_region_code"):
            item.setdefault(field, None)
        register(Organization, item["id"], item)

    for item in data["sources"]:
        item = _record(
            item,
            {
                "id": str,
                "name": str,
                "publisher_organization_id": str,
                "base_url": str,
                "region_codes": list,
                "seeds": list,
            },
            {"description": str},
        )
        seeds = item.pop("seeds")
        codes = item.pop("region_codes")
        if any(not isinstance(code, str) or not code.strip() for code in codes):
            raise CatalogError("region_codes must contain nonblank strings")
        if len(codes) != len(set(codes)):
            raise CatalogError("Duplicate region_codes")
        regions = []
        for code in codes:
            region = session.get(Region, code)
            if region is None:
                raise CatalogError(f"Unknown region: {code!r}")
            regions.append(region)
        item.setdefault("description", "")
        source, is_new = register(Source, item["id"], item)
        if is_new:
            source.region_tags = regions
        elif {region.code for region in source.region_tags} != set(codes):
            raise CatalogError(f"Conflicting Source {source.id!r}: region_codes")

        for seed_data in seeds:
            seed_data = _record(
                seed_data,
                {"id": str, "name": str, "url": str, "kind": str},
                {"refresh_interval_seconds": int, "discovery_rule": str},
            )
            seed_data["kind"] = SeedKind(seed_data["kind"])
            seed_data.setdefault("refresh_interval_seconds", 3600)
            seed_data.setdefault("discovery_rule", None)
            url = seed_data.pop("url")
            resource = session.scalar(
                select(Resource).where(Resource.normalized_url == normalize_url(url))
            )
            if resource is None:
                resource = Resource(
                    original_url=url,
                    source=source,
                    kind=ResourceKind.PAGE
                    if seed_data["kind"] == SeedKind.LISTING
                    else ResourceKind(seed_data["kind"].value),
                )
                session.add(resource)
                session.flush()
                created["resources"] += 1
            elif resource.source_id != source.id:
                raise CatalogError("Seed URL already belongs to another or unassigned source")
            seed_data.update(source_id=source.id, resource_id=resource.id)
            register(Seed, seed_data["id"], seed_data)
    session.flush()
    return created
