def build_pagination_payload(total: int, page: int, page_size: int, items, legacy_key: str = None):
    total_pages = (total + page_size - 1) // page_size if total else 0
    payload = {
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_next": page < total_pages,
        "data": items,
        "total_count": total,
        "total_pages": total_pages,
    }
    if legacy_key:
        payload[legacy_key] = items
    return payload
