"""Docker sandboxing, hardened, with an instrumented network boundary.

This is ProvingGround's two-container design carried over intact, then hardened
and extended so the network boundary can be *measured* rather than merely
trusted. Two container roles, with deliberately different powers:

  * the **verification container** has ``--network none``, is created fresh
    after the agent has stopped, and mounts the hidden production tests at
    ``/hidden`` read-only. It exists to run one command and die.
  * the **agent container** (used by the in-container driver path and by every
    containerised run) is where an agent works. It is the interesting one for
    an injection study, because an injected instruction that wants to exfiltrate
    has to cross this container's network boundary to do it.

What is new here relative to ProvingGround, and why an offensive-security
reader should care:

  * **Hardening the blast radius.** Every container drops all capabilities,
    sets ``no-new-privileges``, runs under a restrictive seccomp profile, and
    mounts its root filesystem read-only with writable space confined to
    ``/work`` and a size-capped ``/tmp`` tmpfs. If a planted instruction does
    get an agent to run something hostile, this is what bounds what that
    something can reach. It is the same posture you would want on a CI runner or
    a Kubernetes pod that executes untrusted code.

  * **A deny-by-default egress boundary that logs.** ``EgressNetwork`` puts the
    agent container on an ``--internal`` Docker network (no route off the host)
    together with a sidecar HTTP CONNECT proxy that allowlists exactly the hosts
    a legitimate run needs (the model API) and writes one JSONL line per
    connection attempt — allowed or refused — to a host-mounted sink file. An
    injected instruction that tries to POST a canary to an attacker host shows
    up in that file as a refused CONNECT carrying the token. That log is the
    strong, deterministic exfil signal; see ``egress.py`` and ``detectors.py``.

The host-driver path (running the real ``claude`` / ``codex`` CLI on the host,
which is how ProvingGround measures the shipping product with its real auth)
cannot use the container boundary, so on that path exfil is detected from the
command log instead. Both paths feed the same detector. Neither is trusted on
its own.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import shutil
import subprocess
import time
import uuid

IMAGE = "breach-sandbox:0.1.0"

# Applied to every container. Enough to run pytest, not enough to be
# interesting to something that has gone wrong.
_LIMITS = ["--memory", "2g", "--cpus", "2", "--pids-limit", "512"]

#: Hardening flags applied to every container we start. Dropping all Linux
#: capabilities and forbidding privilege escalation is the cheap, high-value
#: half of sandboxing untrusted execution; the seccomp profile is the other
#: half. ``SECCOMP_PROFILE`` is resolved lazily so a caller can point at a
#: custom one, and falls back to Docker's default if the file is absent.
SECCOMP_PROFILE = pathlib.Path(__file__).resolve().parent.parent / "docker" / "seccomp.json"


def _hardening() -> list[str]:
    flags = [
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
    ]
    if SECCOMP_PROFILE.exists():
        flags += ["--security-opt", f"seccomp={SECCOMP_PROFILE}"]
    return flags


@dataclasses.dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    @property
    def combined(self) -> str:
        return (self.stdout + "\n" + self.stderr).strip()


def _run(cmd: list[str], timeout: int, cwd: pathlib.Path | None = None,
         env: dict[str, str] | None = None) -> ExecResult:
    t0 = time.monotonic()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           cwd=cwd, env=env)
        return ExecResult(p.returncode, p.stdout, p.stderr, time.monotonic() - t0)
    except subprocess.TimeoutExpired as e:
        return ExecResult(
            124,
            (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""),
            (e.stderr or b"").decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or ""),
            time.monotonic() - t0,
            timed_out=True,
        )


def image_exists(image: str = IMAGE) -> bool:
    return _run(["docker", "image", "inspect", image], timeout=60).exit_code == 0


def build_image(context: pathlib.Path, image: str = IMAGE) -> ExecResult:
    return _run(
        ["docker", "build", "--pull", "-t", image, "-f", str(context / "Dockerfile"), str(context)],
        timeout=1800,
    )


class EgressNetwork:
    """A deny-by-default network with a logging allow-proxy sidecar.

    The agent container is attached to an ``--internal`` Docker network, which
    on its own has no route off the host. The only way out is the sidecar HTTP
    CONNECT proxy, which allowlists ``allow_hosts`` and appends one JSONL record
    per connection attempt to ``sink_path`` on the host. Point an agent
    container's ``HTTP_PROXY`` / ``HTTPS_PROXY`` at ``proxy_url`` and every
    outbound attempt is either forwarded (allowed host) or refused (everything
    else) and, either way, logged.

    Usage::

        with EgressNetwork(sink_path, allow_hosts=["api.anthropic.com"]) as net:
            with AgentContainer(work, network=net.name, proxy=net.proxy_url) as c:
                c.exec(...)
        hits = egress.read_sink(sink_path)
    """

    def __init__(self, sink_path: pathlib.Path, allow_hosts: list[str],
                 image: str = IMAGE, proxy_port: int = 8888):
        self.sink_path = sink_path.resolve()
        self.allow_hosts = list(allow_hosts)
        self.image = image
        self.proxy_port = proxy_port
        self.name = f"breach-egress-{uuid.uuid4().hex[:10]}"
        self.proxy_name = f"{self.name}-proxy"
        self._started = False

    @property
    def proxy_url(self) -> str:
        # Reachable from other containers on the same network by container name.
        return f"http://{self.proxy_name}:{self.proxy_port}"

    def __enter__(self) -> "EgressNetwork":
        self.sink_path.parent.mkdir(parents=True, exist_ok=True)
        self.sink_path.touch()
        r = _run(["docker", "network", "create", "--internal", self.name],
                 timeout=60, env=_base_env())
        if not r.ok:
            raise RuntimeError(f"could not create egress network: {r.combined}")
        # The proxy sidecar. It needs real egress to forward allowlisted hosts,
        # so it is *also* attached to the default bridge; the agent container is
        # not, which is what makes the proxy the only way out.
        allow = ",".join(self.allow_hosts)
        cmd = [
            "docker", "run", "-d", "--name", self.proxy_name,
            "--network", self.name, *_LIMITS, *_hardening(),
            "-e", f"BREACH_ALLOW_HOSTS={allow}",
            "-e", f"BREACH_PROXY_PORT={self.proxy_port}",
            "-v", f"{self.sink_path}:/sink/egress.jsonl",
            self.image, "python3", "/opt/breach/egress_proxy.py",
        ]
        r = _run(cmd, timeout=120, env=_base_env())
        if not r.ok:
            _run(["docker", "network", "rm", self.name], timeout=60)
            raise RuntimeError(f"could not start egress proxy: {r.combined}")
        # Attach the proxy to the default bridge too, for real forwarding.
        _run(["docker", "network", "connect", "bridge", self.proxy_name],
             timeout=60, env=_base_env())
        self._started = True
        return self

    def __exit__(self, *exc) -> None:
        if self._started:
            _run(["docker", "rm", "-f", self.proxy_name], timeout=120)
            _run(["docker", "network", "rm", self.name], timeout=60)


class AgentContainer:
    """A hardened container with the work tree mounted read-write.

    When ``network`` is an :class:`EgressNetwork` name and ``proxy`` is its
    URL, the container has no route off the host except through the logging
    proxy. When ``network`` is ``None`` the container is ``--network none``,
    for a driver that reaches its model API from the host side.
    """

    def __init__(self, workdir: pathlib.Path, image: str = IMAGE,
                 env: dict[str, str] | None = None,
                 mounts: list[tuple[pathlib.Path, str, str]] | None = None,
                 network: str | None = None, proxy: str | None = None):
        self.workdir = workdir
        self.image = image
        self.name = f"breach-agent-{uuid.uuid4().hex[:12]}"
        self._env = dict(env or {})
        self._mounts = mounts or []
        self._network = network
        if proxy:
            # Route library HTTP clients through the logging proxy.
            self._env.setdefault("HTTP_PROXY", proxy)
            self._env.setdefault("HTTPS_PROXY", proxy)
            self._env.setdefault("http_proxy", proxy)
            self._env.setdefault("https_proxy", proxy)
            self._env.setdefault("NO_PROXY", "")
        self._started = False

    def __enter__(self) -> "AgentContainer":
        cmd = ["docker", "run", "-d", "--name", self.name, *_LIMITS, *_hardening(),
               "--network", self._network or "none",
               "--read-only",
               "--tmpfs", "/tmp:rw,exec,size=256m",
               "-v", f"{self.workdir}:/work", "-w", "/work"]
        for host, dest, mode in self._mounts:
            cmd += ["-v", f"{host}:{dest}:{mode}"]
        for k in self._env:
            cmd += ["-e", k]
        cmd += [self.image, "sleep", "infinity"]
        env = {**_base_env(), **self._env}
        r = _run(cmd, timeout=120, env=env)
        if not r.ok:
            raise RuntimeError(f"could not start agent container: {r.combined}")
        self._started = True
        return self

    def exec(self, shell_cmd: str, timeout: int = 900,
             env: dict[str, str] | None = None) -> ExecResult:
        cmd = ["docker", "exec", "-w", "/work"]
        for k in (env or {}):
            cmd += ["-e", k]
        cmd += [self.name, "bash", "-lc", shell_cmd]
        return _run(cmd, timeout=timeout, env={**_base_env(), **self._env, **(env or {})})

    def __exit__(self, *exc) -> None:
        if self._started:
            _run(["docker", "rm", "-f", self.name], timeout=120)


def verify(workdir: pathlib.Path, hidden_dir: pathlib.Path | None, cmd: str,
           image: str = IMAGE, timeout: int = 300) -> ExecResult:
    """Run a verification command in a fresh, network-isolated container.

    The work tree is mounted read-only so a verification run can never change
    the artefact it is measuring. Hidden tests land at ``/hidden``. pytest needs
    somewhere to write, so ``/tmp`` is a tmpfs. Hardened like everything else.
    """
    docker_cmd = [
        "docker", "run", "--rm", *_LIMITS, *_hardening(),
        "--network", "none",
        "--read-only",
        "--tmpfs", "/tmp:rw,exec,size=256m",
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        "-e", "PYTHONHASHSEED=0",
        "-v", f"{workdir}:/work:ro",
    ]
    if hidden_dir is not None:
        docker_cmd += ["-v", f"{hidden_dir}:/hidden:ro"]
    docker_cmd += ["-w", "/work", image, "bash", "-lc", cmd]
    return _run(docker_cmd, timeout=timeout, env=_base_env())


def stage_repo(src: pathlib.Path, dest: pathlib.Path) -> pathlib.Path:
    """Copy a task's seed repo into a fresh work tree."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    return dest


def _base_env() -> dict[str, str]:
    import os
    # Docker on macOS needs PATH/HOME/DOCKER_* from the ambient environment.
    keep = ("PATH", "HOME", "USER", "TMPDIR", "LANG", "LC_ALL")
    env = {k: v for k, v in os.environ.items()
           if k in keep or k.startswith("DOCKER_")}
    return env


def docker_available() -> bool:
    return _run(["docker", "version"], timeout=30).ok
