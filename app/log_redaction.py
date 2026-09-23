"""Keep signed-link secrets out of access logs.

uvicorn's access log prints the full request path including the query string.
For /d/... that includes the signature, and anyone who can read the logs could
replay the link. This filter replaces the query string of download requests.
"""

import logging

DOWNLOAD_PATH_PREFIX = "/d/"


class RedactSignedUrlQuery(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        # uvicorn access log args: (client_addr, method, path_with_query, http_version, status)
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            path = args[2]
            if path.startswith(DOWNLOAD_PATH_PREFIX) and "?" in path:
                redacted = path.split("?", 1)[0] + "?<redacted>"
                record.args = (*args[:2], redacted, *args[3:])
        return True


def install_access_log_redaction() -> None:
    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactSignedUrlQuery) for f in access_logger.filters):
        access_logger.addFilter(RedactSignedUrlQuery())
