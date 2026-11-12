"""Prometheus-Textformat 0.0.4, handgeschrieben. Fehlende/None-Werte werden ausgelassen."""
from moni.metrics_model import HELP, flatten

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _esc(v) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def sample(name: str, labels, value) -> str:
    lab = ",".join(f'{k}="{_esc(v)}"' for k, v in labels)
    return f"{name}{{{lab}}} {value!r}" if lab else f"{name} {value!r}"


def family(name: str, help_: str, kind: str, samples: list[str]) -> list[str]:
    return [f"# HELP {name} {help_}", f"# TYPE {name} {kind}", *samples]


def build_info(role: str, version: str) -> list[str]:
    return family("moni_build_info", "Bauinformation von Moni, Wert immer 1", "gauge",
                  [sample("moni_build_info", [("role", role), ("version", version)], 1)])


def render(snapshot: dict, role: str | None = None, version: str = "") -> str:
    fam: dict[str, list[str]] = {}
    for s in flatten(snapshot):
        fam.setdefault(s.name, []).append(sample(s.name, s.labels, s.value))

    lines = build_info(role, version) if role else []
    for name, samples in fam.items():
        lines += family(name, HELP[name], "gauge", samples)
    return "\n".join(lines) + "\n"
