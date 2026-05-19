import contextvars

stream_callback_var: contextvars.ContextVar = contextvars.ContextVar("stream_callback", default=None)