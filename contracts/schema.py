"""The contract schemas (contracts/schemas/*.schema.json, JSON Schema) and a small stdlib checker for them.

`clean(type, item)` returns (item, errors): the item cut down to what its type's schema declares (an object with
`"additionalProperties": false` keeps only its declared keys, at any depth, so nothing the contract doesn't name
can leave the source), and every way it breaks the schema. On top of the schema, metadata only (§4): no string
may be a link to a stream or a media file, except a content item's `streams[].url` (§2.3).

The checker knows the JSON Schema keywords the schemas use: $ref (in-file and "<file>#/..."), type, enum,
required, properties, additionalProperties, items, min/maxItems, min/maxLength, minProperties, pattern,
minimum/maximum, format date-time, anyOf/allOf/oneOf. The schema files themselves are plain JSON Schema, so the
app or any other client can check the same documents with a full validator.

Three primary types: `metadata`, `availability`, `content`, one file each
(`<type>.schema.json`) carrying `x-version`, `x-ttl` and `x-kinds`: an item's `kind` picks its shape, a file
`<type>.<shape>.schema.json` (metadata: movie|series|season|episode|event -> metadata.work, list -> metadata.list;
availability: offer, channel, airing; content: movie|episode -> content.play, live -> content.live). A new kind is a line in `x-kinds` (and a
shape file when no shape fits). A shape may carry `x-playable`: `true` lets its items hold stream or media links
anywhere, a list names the only top-level keys that may (content.play: ["streams"], each checked by `streams_ok`);
metadata and availability shapes never may.
"""
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

DIR = Path(__file__).resolve().parent / "schemas"
PRIMARY = ("metadata", "availability", "content")
DATE_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$")
# §4: a response never holds a playable URL. Checked on every string that looks like a link, whatever its key.
MEDIA = (".m3u8", ".m3u", ".mp4", ".m4v", ".ts", ".mkv", ".mov", ".mpd")
STREAM_HOSTS = frozenset()  # hosts that only serve streams; none of today's providers names one
LINKS = re.compile(r"[a-z][a-z0-9+.-]*://[^\s<>\"']+", re.I)  # every link in a string, a sentence included
KINDS = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}

_docs = {}


def document(name):
    if name not in _docs:
        _docs[name] = json.loads((DIR / name).read_text())
    return _docs[name]


def schema_for(contract_type):
    return document(contract_type + ".schema.json")


def types():
    """The primary types: every <type>.schema.json without a shape part, the three first."""
    found = sorted(p.name[:-len(".schema.json")] for p in DIR.glob("*.schema.json")
                   if p.name != "common.schema.json" and "." not in p.name[:-len(".schema.json")])
    return tuple(t for t in PRIMARY if t in found) + tuple(t for t in found if t not in PRIMARY)


def kinds(contract_type):
    """{kind: shape} for a primary type."""
    return schema_for(contract_type).get("x-kinds") or {}


TYPES = types()


def resolve(ref, doc):
    """(schema, document name) for a $ref seen in `doc`."""
    file, _, pointer = ref.partition("#")
    doc = file or doc
    node = document(doc)
    for part in [p for p in pointer.split("/") if p]:
        node = node[part]
    return node, doc


def is_type(value, name):
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, KINDS[name])


def check(value, schema, doc, path, errors):
    """The value cut down to the schema, with every problem appended to errors as "path: what"."""
    if "$ref" in schema:
        target, doc = resolve(schema["$ref"], doc)
        return check(value, target, doc, path, errors)
    kinds = schema.get("type")
    if kinds is not None:
        kinds = kinds if isinstance(kinds, list) else [kinds]
        if not any(is_type(value, k) for k in kinds):
            errors.append("%s: expected %s" % (path, "|".join(kinds)))
            return value
    if "enum" in schema and value not in schema["enum"]:
        errors.append("%s: not one of %s" % (path, schema["enum"]))
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", len(value)):
            errors.append("%s: length %d out of range" % (path, len(value)))
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append("%s: does not match %s" % (path, schema["pattern"]))
        if schema.get("format") == "date-time" and not DATE_TIME.match(value):
            errors.append("%s: not an ISO 8601 time" % path)
    if is_type(value, "number"):
        if "minimum" in schema and value < schema["minimum"] or "maximum" in schema and value > schema["maximum"]:
            errors.append("%s: %s out of range" % (path, value))
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", len(value)):
            errors.append("%s: %d items out of range" % (path, len(value)))
        if "items" in schema:
            value = [check(v, schema["items"], doc, "%s[%d]" % (path, i), errors) for i, v in enumerate(value)]
    if isinstance(value, dict):
        props, extra = schema.get("properties") or {}, schema.get("additionalProperties", True)
        out = {}
        for key, v in value.items():
            if key in props:
                out[key] = check(v, props[key], doc, path + "." + key, errors)
            elif isinstance(extra, dict):
                out[key] = check(v, extra, doc, path + "." + key, errors)
            elif extra is not False:
                out[key] = v
        value = out
        for key in schema.get("required") or []:
            if key not in value or value[key] is None:
                errors.append("%s: %s is required" % (path, key))
        if len(value) < schema.get("minProperties", 0):
            errors.append("%s: needs at least %d fields" % (path, schema["minProperties"]))
    for branch in schema.get("allOf") or []:
        check(value, branch, doc, path, errors)
    for word, need in (("anyOf", lambda n: n >= 1), ("oneOf", lambda n: n == 1)):
        if word in schema:
            passed = sum(1 for branch in schema[word] if not _errors(value, branch, doc, path))
            if not need(passed):
                errors.append("%s: matches %d of %s" % (path, passed, word))
    return value


def _errors(value, schema, doc, path):
    found = []
    check(value, schema, doc, path, found)
    return found


def playable(value, path="item"):
    """Every place in value where a string links to a stream or a media file."""
    found = []
    if isinstance(value, dict):
        for k, v in value.items():
            found += playable(v, path + "." + k)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            found += playable(v, "%s[%d]" % (path, i))
    elif isinstance(value, str) and "://" in value:
        for link in LINKS.findall(value):  # the string as a link, or the links inside its prose
            try:
                parts = urlsplit(link.rstrip(".,;:!?)"))
            except ValueError:
                return [path + ": unreadable link"]
            if parts.path.casefold().endswith(MEDIA) or (parts.hostname or "") in STREAM_HOSTS:
                return [path + ": a playable link"]
    return found


def streams_ok(value):
    """Problems with a content item's streams: each must be https and name the format its path has."""
    found = []
    for i, stream in enumerate(value.get("streams") or []):
        if not isinstance(stream, dict):
            continue
        path = urlsplit(str(stream.get("url") or "")).path.casefold()
        want = {"hls": (".m3u8",), "mp4": (".mp4", ".m4v")}.get(stream.get("format"), ())
        if not path.endswith(want):
            found.append("item.streams[%d]: the url is not a %s stream" % (i, stream.get("format")))
    return found


def clean(contract_type, item):
    """(the item cut down to its type's schema, [errors]); errors empty means it may be served."""
    errors = []
    shape = kinds(contract_type).get(item.get("kind")) if isinstance(item, dict) else None
    if shape is None:
        return item, ["item.kind: %r is not a kind of %s" % (item.get("kind") if isinstance(item, dict) else None,
                                                             contract_type)]
    schema = schema_for(shape)
    value = check(item, schema, shape + ".schema.json", "item", errors)
    allowed = schema.get("x-playable")
    if allowed is True:
        return value, errors
    if allowed and isinstance(value, dict):  # a list: only those top-level keys may hold playable links (§2.3)
        rest = {k: v for k, v in value.items() if k not in allowed}
        return value, errors + streams_ok(value) + playable(rest)
    return value, errors + playable(value)
