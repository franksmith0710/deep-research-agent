from __future__ import annotations

from dataclasses import dataclass


VIKING_SCHEME = "viking"


@dataclass(frozen=True)
class VikingURI:
    """viking://{scope}/{path}  URI 解析与操作。"""

    scope: str        # user / session / resources / agent
    path: str         # /memories/entities/surface_code

    @classmethod
    def parse(cls, uri: str) -> "VikingURI":
        if not uri.startswith(f"{VIKING_SCHEME}://"):
            raise ValueError(f"Invalid Viking URI: {uri}")
        rest = uri[len(f"{VIKING_SCHEME}://"):]
        if "/" not in rest:
            return cls(scope=rest, path="")
        scope, _, path = rest.partition("/")
        return cls(scope=scope, path="/" + path if path else "")

    @property
    def full(self) -> str:
        return f"{VIKING_SCHEME}://{self.scope}{self.path}"

    @property
    def parent(self) -> "VikingURI":
        if not self.path or self.path == "/":
            return VikingURI(scope=self.scope, path="")
        parent_path = "/".join(self.path.rstrip("/").split("/")[:-1])
        return VikingURI(scope=self.scope, path=parent_path or "")

    @property
    def name(self) -> str:
        if not self.path or self.path == "/":
            return self.scope
        return self.path.rstrip("/").split("/")[-1]

    def child(self, name: str) -> "VikingURI":
        base = self.path.rstrip("/")
        return VikingURI(scope=self.scope, path=f"{base}/{name}")

    def is_descendant_of(self, other: "VikingURI") -> bool:
        if self.scope != other.scope:
            return False
        return self.full.startswith(other.full.rstrip("/") + "/")

    def __str__(self) -> str:
        return self.full
