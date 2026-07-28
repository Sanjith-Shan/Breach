# breach-sandbox:0.1.0
#
# One image, two roles, both driven by breach/sandbox.py:
#   * agent / verification container: `python -m pytest -q` against a
#     mounted task repo (see sandbox.verify() and AgentContainer).
#   * egress proxy sidecar: `python3 /opt/breach/egress_proxy.py`, started by
#     EgressNetwork as the only route off an agent container's internal
#     network.
#
# Mirrors ProvingGround's verification image (same base, same determinism
# flags) with the egress proxy added and pyyaml pulled in for task fixtures.
FROM python:3.12.8-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=0 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=UTC \
    LANG=C.UTF-8

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY requirements-sandbox.txt /tmp/requirements-sandbox.txt
RUN pip install --no-cache-dir -r /tmp/requirements-sandbox.txt

# Agents (and the pytest runs inside verify()) run as a non-root user so a
# stray `rm -rf /` is boring rather than educational. This is on top of the
# --read-only / --cap-drop ALL / seccomp hardening applied at `docker run`
# time in breach/sandbox.py; the Dockerfile only sets up the image, the
# hardening flags are what actually enforce it per container.
RUN useradd -m -u 1000 agent

# The egress proxy sidecar. It is started with an explicit `python3
# /opt/breach/egress_proxy.py` command (see EgressNetwork in
# breach/sandbox.py), so it does not need to be the image ENTRYPOINT/CMD --
# it just needs to exist at this fixed path.
COPY egress_proxy.py /opt/breach/egress_proxy.py

WORKDIR /work

# No ENTRYPOINT/CMD: breach/sandbox.py always passes an explicit command
# (`bash -lc "..."` for pytest runs, `sleep infinity` + `docker exec` for the
# agent container, or the egress proxy invocation for the sidecar).
