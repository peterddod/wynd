"""JSON Schema helpers: normalisation, path walking, nullability ($DRAFTS/01 §7.9, §8)."""

from wynd.spec import ANY, MISSING, is_nullable, normalize_schema, schema_at, strip_null

RECURSIVE = {
    "type": "object",
    "properties": {"node": {"$ref": "#/$defs/Node"}},
    "$defs": {
        "Node": {
            "title": "Node",
            "type": "object",
            "properties": {"child": {"anyOf": [{"$ref": "#/$defs/Node"}, {"type": "null"}]}, "v": {"type": "integer"}},
        }
    },
}


def test_normalize_inlines_refs_and_drops_noise():
    schema = {
        "title": "Done",
        "description": "doc",
        "type": "object",
        "properties": {
            "exit": {"const": "done", "default": "done", "title": "Exit", "type": "string"},
            "title": {"title": "Title", "type": "string", "description": "a property named title stays"},
            "item": {"$ref": "#/$defs/Item"},
        },
        "required": ["title", "item"],
        "$defs": {"Item": {"title": "Item", "type": "object", "properties": {"b": {"type": "integer"},
                                                                             "a": {"type": "string"}},
                           "required": ["b", "a"], "examples": [{}]}},
    }
    assert normalize_schema(schema) == {
        "type": "object",
        "properties": {
            "exit": {"const": "done"},
            "title": {"type": "string"},
            "item": {"type": "object", "properties": {"b": {"type": "integer"}, "a": {"type": "string"}},
                     "required": ["a", "b"]},
        },
        "required": ["item", "title"],
    }


def test_normalize_drops_exit_and_empty_required_and_keeps_recursive_refs():
    assert normalize_schema({"type": "object", "properties": {"exit": {"const": "x"}}, "required": ["exit"]}) == {
        "type": "object", "properties": {"exit": {"const": "x"}}}
    normalized = normalize_schema(RECURSIVE)
    child = normalized["properties"]["node"]["properties"]["child"]
    assert child == {"anyOf": [{"$ref": "#/$defs/Node"}, {"type": "null"}]}


def test_schema_at():
    schema = {
        "type": "object",
        "properties": {
            "fields": {"type": "object"},
            "items": {"type": "array", "items": {"type": "object", "properties": {"sku": {"type": "string"}}}},
            "maybe": {"anyOf": [{"type": "object", "properties": {"x": {"type": "number"}}}, {"type": "null"}]},
            "name": {"type": "string"},
        },
    }
    assert schema_at(schema, ["name"]) == {"type": "string"}
    assert schema_at(schema, ["fields", "total", "deep"]) is ANY
    assert schema_at({}, ["anything"]) is ANY
    assert schema_at(schema, ["items", 0, "sku"]) == {"type": "string"}
    assert schema_at(schema, ["items", None, "sku"]) == {"type": "string"}
    assert schema_at(schema, ["items", 0, "nope"]) is MISSING
    assert schema_at(schema, ["maybe", "x"]) == {"type": "number"}
    assert schema_at(schema, ["maybe"]) == schema["properties"]["maybe"]
    assert schema_at(schema, ["totl"]) is MISSING
    assert schema_at(schema, ["name", "x"]) is MISSING
    assert schema_at(schema, ["name", 0]) is MISSING
    assert schema_at(RECURSIVE, ["node", "v"]) == {"type": "integer"}
    assert schema_at(RECURSIVE, ["node", "child", "v"]) is ANY  # recursive ref
    assert schema_at(schema, []) is schema


def test_nullability():
    assert is_nullable({"anyOf": [{"type": "string"}, {"type": "null"}]})
    assert is_nullable({"type": ["string", "null"]})
    assert is_nullable({"type": "null"})
    assert not is_nullable({"type": "string"})
    assert not is_nullable({})
    assert strip_null({"anyOf": [{"type": "string"}, {"type": "null"}], "default": None}) == {"type": "string"}
    assert strip_null({"type": ["string", "null"]}) == {"type": "string"}
    assert strip_null({"anyOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}]}) == {
        "anyOf": [{"type": "string"}, {"type": "integer"}]}
    assert strip_null({"type": "string"}) == {"type": "string"}
