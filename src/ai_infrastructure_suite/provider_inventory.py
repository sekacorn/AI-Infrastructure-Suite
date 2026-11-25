"""Reading and validating ``provider-inventory.v1`` evidence.

A provider inventory lists the model providers a system calls: which provider,
where its endpoint is, which models, whether an OpenAI-compatible fallback is
declared, and which features are in use. It is the evidence behind the
``model_providers`` lock-in vector. This module only reads and validates it;
scoring is a later checkpoint.

Everything in an inventory is the operator's declaration, with one exception:
**hosting is derived, not declared.** The scanner classifies each endpoint
origin itself, with the same rule AgentForge and ModelSwapBench apply. A
declaration may only add hosted-ness. Declaring ``local`` for a public endpoint
is refused, because whoever owns the server, the content still leaves the
machine.

Jurisdiction is always reported as attested and is never scored here.

This module never opens a socket and never resolves a name. Error messages
never repeat a value from the document, because an endpoint field is exactly
where a misplaced credential would be.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

from ai_infrastructure_suite._evidence import load_json_document, read_bounded_file

SCHEMA_VERSION: Final = "provider-inventory.v1"
MAX_INVENTORY_BYTES: Final = 256 * 1024
MAX_PROVIDERS: Final = 64
MAX_MODELS_PER_PROVIDER: Final = 64
MAX_VENDOR_ONLY_FEATURES: Final = 16
MAX_TEXT_CHARS: Final = 256

#: AgentForge's provider registry keys, plus ``other`` for anything else.
PROVIDER_KINDS: Final = frozenset(
    {"openai", "openai_compatible", "anthropic", "bedrock", "ollama", "echo", "other"}
)
#: Kinds whose SDK supplies its own hosted default endpoint, so an inventory may
#: leave ``endpoint_origin`` null for them. The endpoint is then the vendor's.
SDK_DEFAULT_HOSTED_KINDS: Final = frozenset({"openai", "anthropic", "bedrock"})
#: Offline, deterministic providers with no endpoint at all.
OFFLINE_KINDS: Final = frozenset({"echo"})
PRESET_SOURCES: Final = frozenset({"built_in", "user"})
HOSTING: Final = frozenset({"local", "hosted"})
JURISDICTION_LITERALS: Final = frozenset({"unknown", "not_applicable"})
RECONSTRUCTION: Final = frozenset({"documented", "none"})

#: 0.0.0.0 is accepted because development servers bind it to mean "this host".
#: It is a host literal compared against, never an address this code binds.
LOCAL_HOSTNAMES: Final = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})  # noqa: S104  # nosec B104

_ID = re.compile(r"[a-z0-9_]{1,64}")
_JURISDICTION = re.compile(r"[A-Z]{2}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class ProviderInventoryError(ValueError):
    """A provider inventory is malformed, inconsistent, or exceeds a bound."""


#: Documentation (RFC 5737, RFC 3849), benchmarking (RFC 2544), reserved
#: (RFC 1112, 240.0.0.0/4) and limited broadcast (255.255.255.255) addresses.
#: Python's ``is_private`` includes them, but they are neither this machine nor
#: a private network, so an endpoint there is hosted. The same ranges are
#: excluded in AgentForge, ModelSwapBench and the AI Infrastructure Suite, and
#: the shared ``endpoint-locality-cases.v1`` table pins all three.
NOT_LOCAL_NETWORKS: Final = tuple(
    ipaddress.ip_network(network)
    for network in (
        "192.0.2.0/24",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "198.18.0.0/15",
        "240.0.0.0/4",
        "255.255.255.255/32",
        "2001:db8::/32",
    )
)


def _in_not_local_network(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Whether ``address``, or the IPv4 address it maps, is in a range above."""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return any(address in network for network in NOT_LOCAL_NETWORKS)


def origin_is_local(origin: str) -> bool:
    """Whether an endpoint origin is this machine or a private network.

    The same rule as AgentForge's and ModelSwapBench's ``endpoint_is_local``:
    loopback, the unspecified address, link-local, or a private range. A name
    is never resolved. Anything not positively local, including something that
    cannot be parsed, is hosted.
    """
    try:
        host = urlsplit(origin).hostname or ""
    except ValueError:
        return False
    if host in LOCAL_HOSTNAMES:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if _in_not_local_network(address):
        return False
    return bool(address.is_private or address.is_loopback or address.is_link_local)


@dataclass(frozen=True)
class VendorOnlyFeature:
    """A feature only this vendor offers, and whether a way off it is documented."""

    feature: str
    reconstruction: str


@dataclass(frozen=True)
class Features:
    """What the system uses from this provider. All of it is attested."""

    tool_calling: bool
    streaming: bool
    vendor_only: tuple[VendorOnlyFeature, ...]


@dataclass(frozen=True)
class Fallback:
    """A declared OpenAI-compatible fallback. Attested; never exercised here."""

    available: bool
    provider_id: str | None


@dataclass(frozen=True)
class InventoryProvider:
    """One provider in use."""

    provider_id: str
    kind: str
    preset: str | None
    preset_source: str | None
    endpoint_origin: str | None
    hosting_declared: str | None
    hosting: str
    jurisdiction: str
    models: tuple[str, ...]
    fallback: Fallback
    features: Features

    @property
    def jurisdiction_verification(self) -> str:
        """Always ``attested``: a jurisdiction is a statement, never a finding."""
        return "attested"

    @property
    def hosting_verification(self) -> str:
        """``derived`` when an endpoint origin was classified, else ``sdk_default``."""
        if self.endpoint_origin is not None:
            return "derived"
        return "offline" if self.kind in OFFLINE_KINDS else "sdk_default"


@dataclass(frozen=True)
class ProviderInventory:
    """A validated provider inventory."""

    generated_by_tool: str
    generated_by_version: str
    providers: tuple[InventoryProvider, ...]

    def provider(self, provider_id: str) -> InventoryProvider:
        for item in self.providers:
            if item.provider_id == provider_id:
                return item
        raise KeyError(provider_id)

    def attested_claims(self) -> tuple[str, ...]:
        """Everything here a reader must take on the operator's word."""
        claims = ["models in use", "features in use", "jurisdiction", "producer"]
        if any(p.features.vendor_only for p in self.providers):
            claims.append("vendor-only features and their reconstruction paths")
        if any(p.fallback.available for p in self.providers):
            claims.append("that the declared fallback works")
        if any(p.hosting_verification == "sdk_default" for p in self.providers):
            claims.append("that an SDK default endpoint is the vendor's")
        return tuple(claims)


# --- field readers -----------------------------------------------------------


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProviderInventoryError(f"Expected an object for {label}.")
    return value


def _keys(
    document: dict[str, Any], required: set[str], label: str, optional: frozenset[str] = frozenset()
) -> None:
    if not required <= set(document) or set(document) - required - optional:
        raise ProviderInventoryError(f"Unexpected or missing fields in {label}.")


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT_CHARS:
        raise ProviderInventoryError(f"Invalid {label}.")
    return value


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ProviderInventoryError(f"Invalid {label}.")
    return value


def _flag(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ProviderInventoryError(f"Invalid {label}.")
    return value


def _enum(value: object, allowed: frozenset[str], label: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ProviderInventoryError(f"Invalid {label}.")
    return value


def _origin(value: object, label: str) -> str:
    """Scheme, host and optional port; nothing else, and never credentials."""
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT_CHARS:
        raise ProviderInventoryError(f"Invalid {label}.")
    try:
        parts = urlsplit(value)
        port = parts.port
        has_credentials = parts.username is not None or parts.password is not None
    except ValueError:
        raise ProviderInventoryError(f"Invalid {label}.") from None
    if has_credentials or "@" in parts.netloc:
        raise ProviderInventoryError(
            f"{label} must not contain credentials; record the origin only."
        )
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ProviderInventoryError(f"{label} must be an http(s) origin.")
    if parts.path or parts.query or parts.fragment or value.endswith(("?", "#", "/")):
        raise ProviderInventoryError(f"{label} must be an origin: no path, query or fragment.")
    if port is not None and port == 0:
        raise ProviderInventoryError(f"Invalid {label}.")
    host = parts.hostname
    canonical_host = f"[{host}]" if ":" in host else host
    canonical = f"{parts.scheme}://{canonical_host}" + (f":{port}" if port is not None else "")
    if value != canonical:
        raise ProviderInventoryError(f"{label} must be written in canonical lowercase form.")
    return value


def _jurisdiction(value: object, label: str) -> str:
    if isinstance(value, str) and (
        _JURISDICTION.fullmatch(value) or value in JURISDICTION_LITERALS
    ):
        return value
    raise ProviderInventoryError(f"Invalid {label}.")


def _models(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or len(value) > MAX_MODELS_PER_PROVIDER:
        raise ProviderInventoryError(f"Invalid {label}.")
    names: list[str] = []
    for item in value:
        entry = _mapping(item, label)
        _keys(entry, {"model"}, label)
        names.append(_text(entry["model"], label))
    if len(set(names)) != len(names):
        raise ProviderInventoryError(f"Duplicate model in {label}.")
    return tuple(names)


def _features(value: object, label: str) -> Features:
    block = _mapping(value, label)
    _keys(block, {"tool_calling", "streaming", "vendor_only"}, label)
    raw = block["vendor_only"]
    if not isinstance(raw, list) or len(raw) > MAX_VENDOR_ONLY_FEATURES:
        raise ProviderInventoryError(f"Invalid {label} vendor_only.")
    vendor_only: list[VendorOnlyFeature] = []
    for item in raw:
        entry = _mapping(item, f"{label} vendor_only")
        _keys(entry, {"feature", "reconstruction"}, f"{label} vendor_only")
        vendor_only.append(
            VendorOnlyFeature(
                feature=_identifier(entry["feature"], f"{label} vendor_only feature"),
                reconstruction=_enum(
                    entry["reconstruction"], RECONSTRUCTION, f"{label} reconstruction"
                ),
            )
        )
    if len({f.feature for f in vendor_only}) != len(vendor_only):
        raise ProviderInventoryError(f"Duplicate vendor-only feature in {label}.")
    return Features(
        tool_calling=_flag(block["tool_calling"], f"{label} tool_calling"),
        streaming=_flag(block["streaming"], f"{label} streaming"),
        vendor_only=tuple(vendor_only),
    )


def _fallback(value: object, label: str) -> Fallback:
    block = _mapping(value, label)
    _keys(block, {"available"}, label, frozenset({"provider_id"}))
    available = _flag(block["available"], f"{label} available")
    if "provider_id" in block:
        if not available:
            raise ProviderInventoryError(f"{label} names a provider but is not available.")
        return Fallback(
            available=True, provider_id=_identifier(block["provider_id"], f"{label} provider_id")
        )
    return Fallback(available=available, provider_id=None)


def _derive_hosting(kind: str, origin: str | None, declared: str | None, label: str) -> str:
    if origin is None:
        if kind in OFFLINE_KINDS:
            derived = "local"
        elif kind in SDK_DEFAULT_HOSTED_KINDS:
            derived = "hosted"
        else:
            raise ProviderInventoryError(f"{label} needs an endpoint_origin for this kind.")
    else:
        if kind in OFFLINE_KINDS:
            raise ProviderInventoryError(f"{label} is offline and takes no endpoint_origin.")
        derived = "local" if origin_is_local(origin) else "hosted"
    if declared == "local" and derived == "hosted":
        raise ProviderInventoryError(
            f"{label} declares local hosting for an endpoint that is not local."
        )
    # A declaration may only add hosted-ness.
    return "hosted" if declared == "hosted" else derived


def _provider(value: object, index: int) -> InventoryProvider:
    label = f"providers[{index}]"
    entry = _mapping(value, label)
    _keys(
        entry,
        {
            "provider_id",
            "kind",
            "preset",
            "preset_source",
            "endpoint_origin",
            "jurisdiction",
            "models",
            "openai_compatible_fallback",
            "features",
        },
        label,
        frozenset({"hosting_declared"}),
    )
    provider_id = _identifier(entry["provider_id"], f"{label} provider_id")
    kind = _enum(entry["kind"], PROVIDER_KINDS, f"{label} kind")
    preset = None if entry["preset"] is None else _identifier(entry["preset"], f"{label} preset")
    preset_source = (
        None
        if entry["preset_source"] is None
        else _enum(entry["preset_source"], PRESET_SOURCES, f"{label} preset_source")
    )
    if (preset is None) != (preset_source is None):
        raise ProviderInventoryError(f"{label} preset and preset_source must be given together.")
    origin = (
        None
        if entry["endpoint_origin"] is None
        else _origin(entry["endpoint_origin"], f"{label} endpoint_origin")
    )
    declared = (
        _enum(entry["hosting_declared"], HOSTING, f"{label} hosting_declared")
        if "hosting_declared" in entry
        else None
    )
    hosting = _derive_hosting(kind, origin, declared, label)
    jurisdiction = _jurisdiction(entry["jurisdiction"], f"{label} jurisdiction")
    if jurisdiction == "not_applicable" and hosting != "local":
        raise ProviderInventoryError(
            f"{label} is hosted, so its jurisdiction cannot be not_applicable."
        )
    fallback = _fallback(entry["openai_compatible_fallback"], f"{label} openai_compatible_fallback")
    if fallback.provider_id == provider_id:
        raise ProviderInventoryError(f"{label} cannot be its own fallback.")
    return InventoryProvider(
        provider_id=provider_id,
        kind=kind,
        preset=preset,
        preset_source=preset_source,
        endpoint_origin=origin,
        hosting_declared=declared,
        hosting=hosting,
        jurisdiction=jurisdiction,
        models=_models(entry["models"], f"{label} models"),
        fallback=fallback,
        features=_features(entry["features"], f"{label} features"),
    )


def parse_provider_inventory(data: bytes) -> ProviderInventory:
    """Validate ``provider-inventory.v1`` bytes and return the inventory."""
    if len(data) > MAX_INVENTORY_BYTES:
        raise ProviderInventoryError("Provider inventory exceeds the size limit.")
    try:
        document = _mapping(load_json_document(data, "provider inventory"), "provider inventory")
    except ProviderInventoryError:
        raise
    except ValueError as exc:
        raise ProviderInventoryError(str(exc)) from None
    _keys(document, {"schema_version", "generated_by", "providers"}, "provider inventory")
    if document["schema_version"] != SCHEMA_VERSION:
        raise ProviderInventoryError(f"Expected a {SCHEMA_VERSION} document.")
    producer = _mapping(document["generated_by"], "generated_by")
    _keys(producer, {"tool", "version"}, "generated_by")
    raw = document["providers"]
    if not isinstance(raw, list) or not raw:
        raise ProviderInventoryError(
            "An inventory lists at least one provider; declare the vector inapplicable instead."
        )
    if len(raw) > MAX_PROVIDERS:
        raise ProviderInventoryError("Too many providers listed.")
    providers = tuple(_provider(item, index) for index, item in enumerate(raw))
    ids = [p.provider_id for p in providers]
    if len(set(ids)) != len(ids):
        raise ProviderInventoryError("Duplicate provider_id.")
    for item in providers:
        if item.fallback.provider_id is not None and item.fallback.provider_id not in ids:
            raise ProviderInventoryError(
                f"The fallback for {item.provider_id} names a provider not in the inventory."
            )
    return ProviderInventory(
        generated_by_tool=_text(producer["tool"], "generated_by tool"),
        generated_by_version=_text(producer["version"], "generated_by version"),
        providers=providers,
    )


def load_provider_inventory(path: Path, *, expected_sha256: str | None = None) -> ProviderInventory:
    """Read and validate an inventory from a bounded local file.

    ``expected_sha256`` binds the inventory to the digest a manifest declared.
    """
    data = read_bounded_file(path, MAX_INVENTORY_BYTES)
    if expected_sha256 is not None:
        if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
            raise ProviderInventoryError("Invalid expected digest.")
        if hashlib.sha256(data).hexdigest() != expected_sha256:
            raise ProviderInventoryError("Provider inventory differs from its declared SHA-256.")
    return parse_provider_inventory(data)
