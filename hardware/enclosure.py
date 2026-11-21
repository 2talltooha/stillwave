"""
enclosure.py - generates the Stillwave enclosure as STL files, in two styles.

    python hardware/enclosure.py
      -> hardware/cube_body.stl, hardware/cube_back.stl   (soft rounded cube)
      -> hardware/orb_body.stl,  hardware/orb_back.stl    (ball with a round fabric front)

Print ONE style (its body + its back). Both work the same way:
  * the front is an open face with a rim: glue thin fabric over it. Radar passes through cloth.
  * inside, the radar stands upright in a groove on a raised block, centred behind the fabric.
    A notch behind the groove leaves room for the header pins and jumper plugs.
  * the ESP32 sits on the floor behind the block. The USB cable leaves through a slot in the
    back cover, which press-fits on. The back cover also has vents (the radar runs warm).

Print orientation: as exported. Body front-face down on the bed, back cover flat. No supports.
All sizes in mm - measure your parts and change the numbers below if they differ.
"""

import math
import os

import trimesh
from manifold3d import CrossSection, JoinType, Manifold

# ---------- measure these ----------
RADAR_H = 23.0      # radar board height, measured off the breadboard (23 x 23 mm, +-1)
RADAR_W = 23.0      # only used for the preview; the groove runs edge to edge
RADAR_T = 1.6       # bare board thickness
# ---------- shared ----------
WALL = 2.0
RIM_T = 2.0         # thickness of the front rim (the fabric glues onto its outside)
FRONT_GAP = 4.0     # space between the fabric and the radar's chips
GROOVE_W = RADAR_T + 1.0
GROOVE_D = 3.0
PIN_NOTCH_W = 30.0  # notch behind the groove: the header pins stick out the back along the bottom edge
BLOCK_W = 50.0      # width of the radar block (the groove is open at both ends)
BACK_T = 2.0
LIP_H = 4.0
LIP_T = 1.6
FIT = 0.35          # clearance so the back cover press-fits
CABLE_W, CABLE_H = 13.0, 28.0   # slot at the bottom of the back cover for the USB cable
SEG = 160

PED_D = RIM_T + FRONT_GAP + GROOVE_W + 3.0   # depth of the radar block
GROOVE_Z = RIM_T + FRONT_GAP


def box(x0, x1, y0, y1, z0, z1):
    return Manifold.cube([x1 - x0, y1 - y0, z1 - z0]).translate([x0, y0, z0])


def radar_block(x_half, ped_top):
    """Raised block with a groove for the radar and a notch for its pins."""
    ped = box(-x_half, x_half, WALL - 0.01, ped_top, RIM_T - 0.01, PED_D)
    groove = box(-x_half - 1, x_half + 1, ped_top - GROOVE_D, ped_top + 1,
                 GROOVE_Z, GROOVE_Z + GROOVE_W)
    notch = box(-PIN_NOTCH_W / 2, PIN_NOTCH_W / 2, ped_top - GROOVE_D - 6, ped_top + 1,
                GROOVE_Z + GROOVE_W - 0.01, PED_D + 1)
    return ped - groove - notch


def vents(z0, z1, xs, y0, y1):
    out = None
    for x in xs:
        s = box(x - 1.5, x + 1.5, y0, y1, z0, z1)
        out = s if out is None else out + s
    return out


# ======================= style 1: soft rounded cube =======================
CUBE = 90.0         # width and height (room for extra parts later)
CUBE_R = 18.0       # corner rounding
CUBE_DEPTH = 72.0   # front to back, without the back cover
CHAMFER = 1.5       # small bevel on the front edge


def cube_profile(offset=0.0):
    s = CUBE - 2 * CUBE_R
    p = CrossSection.square([s, s]).translate([-s / 2, CUBE_R])
    p = p.offset(CUBE_R + offset, JoinType.Round, circular_segments=SEG)
    return p


def cube_body():
    # x = width, y = height (bottom at 0), z = depth (front face at 0, on the print bed)
    outer = Manifold.batch_hull([
        cube_profile(-CHAMFER).extrude(0.01),
        cube_profile().extrude(CUBE_DEPTH - CHAMFER).translate([0, 0, CHAMFER]),
    ])
    cavity = cube_profile(-WALL).extrude(CUBE_DEPTH).translate([0, 0, RIM_T])
    opening = cube_profile(-(WALL + 4.0)).extrude(RIM_T + 2).translate([0, 0, -1])
    body = outer - cavity - opening
    ped_top = CUBE / 2 - RADAR_H / 2 + GROOVE_D
    return body + radar_block(BLOCK_W / 2, ped_top)


def cube_back():
    plate = Manifold.batch_hull([
        cube_profile(-CHAMFER).extrude(0.01),
        cube_profile().extrude(BACK_T - CHAMFER).translate([0, 0, CHAMFER]),
    ])
    ring = (cube_profile(-(WALL + FIT)) - cube_profile(-(WALL + FIT + LIP_T))).extrude(LIP_H)
    back = plate + ring.translate([0, 0, BACK_T])
    back = back - box(-CABLE_W / 2, CABLE_W / 2, -1, CABLE_H, -1, BACK_T + LIP_H + 1)
    back = back - vents(-1, BACK_T + 1, [-30, -20, -10, 0, 10, 20, 30], CABLE_H + 8, CUBE - 16)
    return back


# ======================= style 2: orb =======================
ORB_R = 55.0        # ball radius (110 mm wide)
ORB_CY = 46.0       # ball centre height above the flat bottom
ORB_CZ = 32.0       # ball centre distance behind the flat front (front overhang ~35 deg)
ORB_BACK = 68.0     # where the back is cut off (inner overhang stays under 45 deg)
ORB_OPEN = 38.0     # radius of the fabric opening in the front
SOCKET_R = 37.0     # round socket at the back that the cover's lip plugs into
SOCKET_D = 5.0


def orb_front_r():
    return math.sqrt(ORB_R ** 2 - ORB_CZ ** 2)


def orb_back_r():
    return math.sqrt(ORB_R ** 2 - (ORB_BACK - ORB_CZ) ** 2)


def ball(r):
    return Manifold.sphere(r, SEG).translate([0, ORB_CY, ORB_CZ])


def orb_body():
    big = 300
    outer = ball(ORB_R) ^ box(-big, big, 0, big, 0, ORB_BACK)

    # inside: ball, narrowing at 45 deg into a round socket at the back (no overhangs)
    ri = ORB_R - WALL
    cone_len = ORB_BACK - SOCKET_D - RIM_T
    cone = Manifold.cylinder(cone_len, SOCKET_R + cone_len, SOCKET_R, SEG).translate([0, ORB_CY, RIM_T])
    front_part = ball(ri) ^ cone ^ box(-big, big, WALL, big, RIM_T, ORB_BACK - SOCKET_D + 0.01)
    socket = Manifold.cylinder(SOCKET_D + 2, SOCKET_R, SOCKET_R, SEG).translate([0, ORB_CY, ORB_BACK - SOCKET_D])
    cavity = front_part + socket

    face = CrossSection.circle(ORB_OPEN, SEG).translate([0, ORB_CY]) ^ \
        CrossSection.square([2 * big, big]).translate([-big, WALL + 4.0])
    opening = face.extrude(RIM_T + 2).translate([0, 0, -1])

    body = outer - cavity - opening
    ped_top = ORB_CY - RADAR_H / 2 + GROOVE_D
    block = radar_block(BLOCK_W / 2, ped_top) ^ ball(ri + 0.5)
    return body + block


def orb_back():
    big = 300
    disc = CrossSection.circle(orb_back_r(), SEG).translate([0, ORB_CY]) ^ \
        CrossSection.square([2 * big, big]).translate([-big, 0])
    plate = disc.extrude(BACK_T)
    lip = (CrossSection.circle(SOCKET_R - FIT, SEG) - CrossSection.circle(SOCKET_R - FIT - LIP_T, SEG))
    back = plate + lip.translate([0, ORB_CY]).extrude(LIP_H).translate([0, 0, BACK_T])
    back = back - box(-CABLE_W / 2, CABLE_W / 2, -1, CABLE_H, -1, BACK_T + LIP_H + 1)
    back = back - vents(-1, BACK_T + 1, [-24, -16, -8, 0, 8, 16, 24], ORB_CY - 4, ORB_CY + 24)
    return back


def save(m, path):
    mesh = m.to_mesh()
    t = trimesh.Trimesh(vertices=mesh.vert_properties[:, :3], faces=mesh.tri_verts)
    t.export(path)
    return t


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    parts = {
        "cube_body": cube_body(), "cube_back": cube_back(),
        "orb_body": orb_body(), "orb_back": orb_back(),
    }
    for name, m in parts.items():
        t = save(m, os.path.join(here, name + ".stl"))
        ext = t.bounds[1] - t.bounds[0]
        print(f"{name:10s} {ext[0]:.0f} x {ext[1]:.0f} x {ext[2]:.0f} mm  "
              f"{t.volume / 1000:.0f} cm3  watertight={t.is_watertight}")
    print(f"Cube: radar centre {CUBE / 2:.0f} mm above the bottom")
    print(f"Orb:  radar centre {ORB_CY:.0f} mm above the bottom")
