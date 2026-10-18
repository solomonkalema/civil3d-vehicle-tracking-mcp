"""Design vehicle definitions for the built-in swept path engine.

All dimensions in metres. Rigid vehicles use one body; articulated vehicles
use a tractor body plus a trailer body joined at a kingpin.

The dimensions shipped here are indicative. Before using a result in a
report, open the same vehicle's diagram in Vehicle Tracking (SANRAL library)
and copy its wheelbase, overhangs and lock values into vehicles.json, so the
Python check and the VT run describe the same vehicle.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

USER_FILE = Path(__file__).with_name("vehicles.json")


@dataclass
class Vehicle:
    name: str
    description: str
    width: float
    front_overhang: float  # front bumper to front (steered) axle
    wheelbase: float  # front axle to rear axle (rear axle = centre of rear group)
    rear_overhang: float  # rear axle to rear bumper
    wall_to_wall_radius: float  # outer front corner radius at full lock
    track: float = 0.0  # wheel track (centre to centre); 0 = width - 0.3
    # Articulated vehicles only (kingpin measured from tractor rear axle,
    # positive = ahead of the axle, which is normal for a fifth wheel).
    articulated: bool = False
    kingpin_offset: float = 0.0
    trailer_front_overhang: float = 0.0  # kingpin to trailer front face
    trailer_wheelbase: float = 0.0  # kingpin to trailer axle group centre
    trailer_rear_overhang: float = 0.0  # trailer axle to trailer rear face
    trailer_width: float = 0.0  # 0 = same as tractor
    max_articulation_deg: float = 70.0
    source: str = "indicative - check against VT vehicle diagram"
    notes: list[str] = field(default_factory=list)

    # ---- derived values -------------------------------------------------
    @property
    def length(self) -> float:
        if not self.articulated:
            return self.front_overhang + self.wheelbase + self.rear_overhang
        tractor_front_to_kp = self.front_overhang + self.wheelbase - self.kingpin_offset
        return tractor_front_to_kp + self.trailer_wheelbase + self.trailer_rear_overhang

    @property
    def wheel_track(self) -> float:
        return self.track if self.track > 0 else max(self.width - 0.3, 0.5)

    @property
    def min_rear_axle_radius(self) -> float:
        """Radius of the rear axle centre at full lock, from the wall-to-wall radius.

        Rw^2 = (R + W/2)^2 + (L + Fo)^2  ->  R = sqrt(Rw^2 - (L+Fo)^2) - W/2
        """
        reach = self.wheelbase + self.front_overhang
        inner = self.wall_to_wall_radius**2 - reach**2
        if inner <= 0:
            raise ValueError(f"{self.name}: wall-to-wall radius too small for its wheelbase")
        return math.sqrt(inner) - self.width / 2

    @property
    def max_steer_deg(self) -> float:
        return math.degrees(math.atan(self.wheelbase / self.min_rear_axle_radius))

    @property
    def max_curvature(self) -> float:
        """Largest curvature the rear axle path may have (1/m)."""
        return 1.0 / self.min_rear_axle_radius

    def summary(self) -> dict:
        d = asdict(self)
        d.update(
            length=round(self.length, 3),
            min_rear_axle_radius=round(self.min_rear_axle_radius, 3),
            max_steer_deg=round(self.max_steer_deg, 1),
        )
        return d


# MoWT RDM Table 5-1 / SANRAL naming, as used on the Leceny and Biplous jobs.
BUILTIN: dict[str, Vehicle] = {
    "P": Vehicle(
        name="P",
        description="Passenger car, 4.8 x 1.8 m",
        width=1.80,
        front_overhang=0.90,
        wheelbase=2.80,
        rear_overhang=1.10,
        wall_to_wall_radius=6.8,
    ),
    "SU": Vehicle(
        name="SU",
        description="Single unit truck, 9.1 x 2.5 m",
        width=2.50,
        front_overhang=1.20,
        wheelbase=6.10,
        rear_overhang=1.80,
        wall_to_wall_radius=10.0,
    ),
    "WB-15": Vehicle(
        name="WB-15",
        description="Semi-trailer, 17.0 x 2.5 m",
        width=2.50,
        front_overhang=0.90,
        wheelbase=3.80,
        rear_overhang=0.60,
        wall_to_wall_radius=11.0,
        articulated=True,
        kingpin_offset=0.30,
        trailer_front_overhang=1.00,
        trailer_wheelbase=10.60,
        trailer_rear_overhang=2.00,
    ),
}


def _load_user() -> dict[str, Vehicle]:
    if not USER_FILE.exists():
        return {}
    raw = json.loads(USER_FILE.read_text(encoding="utf-8"))
    return {k: Vehicle(**v) for k, v in raw.items()}


def all_vehicles() -> dict[str, Vehicle]:
    vs = dict(BUILTIN)
    vs.update(_load_user())  # user definitions override built-ins of the same name
    return vs


def get_vehicle(name: str) -> Vehicle:
    vs = all_vehicles()
    if name not in vs:
        raise KeyError(f"Unknown vehicle '{name}'. Known: {', '.join(sorted(vs))}")
    return vs[name]


def save_user_vehicle(v: Vehicle) -> None:
    v.min_rear_axle_radius  # raises if geometry is impossible
    raw = {}
    if USER_FILE.exists():
        raw = json.loads(USER_FILE.read_text(encoding="utf-8"))
    raw[v.name] = asdict(v)
    USER_FILE.write_text(json.dumps(raw, indent=2), encoding="utf-8")
