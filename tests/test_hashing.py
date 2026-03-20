from app.utils.hashing import canonical_json_dumps, payload_hash


def test_payload_hash_deterministic_for_dict_key_order():
    a = {"b": 1, "a": 2}
    b = {"a": 2, "b": 1}

    assert canonical_json_dumps(a) == canonical_json_dumps(b)
    assert payload_hash(a) == payload_hash(b)

