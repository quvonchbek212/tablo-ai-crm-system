import uuid
from decimal import Decimal
from datetime import datetime, date, time, timedelta
from typing import Any, Dict, List

def serialize_db_value(val: Any) -> Any:
    """
    Recursively converts any database or Python value into a strict JSON-serializable type.
    Solves PydanticSerializationError for:
    - memoryview (from PostgreSQL bytea/geometry)
    - bytes, bytearray
    - Decimal
    - UUID
    - datetime, date, time, timedelta
    - numpy scalars
    - arbitrary un-serializable objects
    """
    if val is None:
        return None

    # Handle numpy scalars if numpy is present
    if hasattr(val, "item") and callable(getattr(val, "item")) and not isinstance(val, (dict, list, tuple)):
        try:
            val = val.item()
        except Exception:
            pass

    # Direct primitives
    if isinstance(val, (bool, int, float, str)):
        return val

    # Binary types (bytea, geometry, blobs)
    if isinstance(val, (memoryview, bytearray)):
        b = bytes(val)
        if len(b) > 128:
            return f"<binary {len(b)} bytes>"
        return b.hex()

    if isinstance(val, bytes):
        if len(val) > 128:
            return f"<binary {len(val)} bytes>"
        return val.hex()

    # Numbers & Decimals
    if isinstance(val, Decimal):
        return int(val) if val % 1 == 0 else float(val)

    # Dates & Times
    if isinstance(val, (datetime, date, time)):
        return val.isoformat()

    if isinstance(val, timedelta):
        return str(val)

    # UUIDs
    if isinstance(val, uuid.UUID):
        return str(val)

    # Iterables
    if isinstance(val, (list, tuple, set)):
        return [serialize_db_value(x) for x in val]

    # Mappings
    if isinstance(val, dict):
        return {str(k): serialize_db_value(v) for k, v in val.items()}

    # Fallback to string representation
    try:
        return str(val)
    except Exception:
        return repr(val)

def clean_data_structure(data: Any) -> Any:
    """Recursively cleans entire list/dict structures."""
    return serialize_db_value(data)
