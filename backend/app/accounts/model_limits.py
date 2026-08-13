INVALID_MODEL_LIMIT_VALUES = {"[object Object]", "undefined", "null"}


def normalize_model_limits(value):
    if not isinstance(value, list):
        return []

    result = []
    seen = set()
    for item in value:
        if not isinstance(item, str):
            continue
        model_id = item.strip()
        if not model_id or model_id in INVALID_MODEL_LIMIT_VALUES or model_id in seen:
            continue
        seen.add(model_id)
        result.append(model_id)
    return result
