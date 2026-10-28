ARG MEMOS_BASE_IMAGE
FROM ${MEMOS_BASE_IMAGE}
ARG MEMOS_BASE_IMAGE
RUN python -c "import re,sys; assert re.fullmatch(r'.+@sha256:[0-9a-f]{64}', sys.argv[1]), 'Pin MEMOS_BASE_IMAGE to a verified registry digest'" "${MEMOS_BASE_IMAGE}"
COPY soft_delete.py api.py models.py query.py /app/src/memos/dashboard_memory/
COPY patch.py /tmp/dashboard-memory-patch.py
RUN python /tmp/dashboard-memory-patch.py
