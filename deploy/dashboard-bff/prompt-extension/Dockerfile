ARG MEMOS_BASE_IMAGE
FROM ${MEMOS_BASE_IMAGE}
ARG MEMOS_BASE_IMAGE
RUN python -c "import re,sys; assert re.fullmatch(r'.+@sha256:[0-9a-f]{64}', sys.argv[1]), 'Pin MEMOS_BASE_IMAGE to a verified registry digest'" "${MEMOS_BASE_IMAGE}"
COPY store.py runtime.py api.py /app/src/memos/dashboard_prompts/
COPY patch.py /tmp/dashboard-prompt-patch.py
RUN python /tmp/dashboard-prompt-patch.py /app/src/memos
