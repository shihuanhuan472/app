import threading

from qwen_token_counter import TokenCounter


_thread_state = threading.local()


def _get_token_counter() -> TokenCounter:
    counter = getattr(_thread_state, "counter", None)
    if counter is None:
        counter = TokenCounter()
        _thread_state.counter = counter
    return counter


def get_token_count(text: str) -> int:
    return int(_get_token_counter().count_tokens(text or ""))
