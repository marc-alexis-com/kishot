"""Tests of the board reading; they need neither KiCad nor ImageMagick."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import kishot  # noqa: E402

BOARD = """(kicad_pcb
\t(version 20241229)
\t(layers
\t\t(0 "F.Cu" signal)
\t\t(4 "In1.Cu" power "GND")
\t\t(6 "In2.Cu" signal)
\t\t(2 "B.Cu" signal)
\t\t(25 "Edge.Cuts" user)
\t)
\t(title_block
\t\t(title "Demo (board)")
\t\t(rev "B")
\t\t(company "")
\t)
\t(footprint "R_0603"
\t\t(layer "F.Cu")
\t\t(property "Reference" "R1 (pull-up)")
\t)
\t(footprint "C_0603"
\t\t(layer "B.Cu")
\t)
\t(gr_rect
\t\t(start 10 20)
\t\t(end 60 50)
\t\t(layer "Edge.Cuts")
\t)
\t(segment
\t\t(start 11 21)
\t\t(end 12 22)
\t\t(layer "F.Cu")
\t)
\t(segment
\t\t(start 11 21)
\t\t(end 12 22)
\t\t(layer "F.Cu")
\t)
\t(arc
\t\t(start 11 21)
\t\t(mid 12 21.5)
\t\t(end 13 22)
\t\t(layer "B.Cu")
\t)
\t(zone
\t\t(net_name "GND")
\t\t(layers "F.Cu" "B.Cu")
\t\t(polygon
\t\t\t(pts
\t\t\t\t(xy 10 20) (xy 60 20) (xy 60 50) (xy 10 50)
\t\t\t)
\t\t)
\t\t(filled_polygon
\t\t\t(layer "F.Cu")
\t\t\t(pts
\t\t\t\t(xy 10 20) (xy 60 20) (xy 60 50)
\t\t\t)
\t\t)
\t\t(filled_polygon
\t\t\t(layer "B.Cu")
\t\t\t(pts
\t\t\t\t(xy 10 20) (xy 60 20) (xy 60 50)
\t\t\t)
\t\t)
\t)
\t(zone
\t\t(net_name "+3V3")
\t\t(layer "In2.Cu")
\t\t(polygon
\t\t\t(pts
\t\t\t\t(xy 10 20) (xy 12 20) (xy 12 22) (xy 10 22)
\t\t\t)
\t\t)
\t)
\t(zone
\t\t(net_name "")
\t\t(layers "*.Cu")
\t\t(keepout (tracks not_allowed))
\t\t(polygon
\t\t\t(pts
\t\t\t\t(xy 10 20) (xy 60 20) (xy 60 50) (xy 10 50)
\t\t\t)
\t\t)
\t)
)
"""


class Parsing(unittest.TestCase):
    def setUp(self):
        self.items = kishot.top_level_items(BOARD)

    def test_parentheses_in_strings_are_ignored(self):
        s = '(a "x ) (" (b))'
        self.assertEqual(kishot.closing_paren(s, 0), len(s) - 1)
        heads = [h for h, _ in self.items]
        self.assertEqual(heads.count("footprint"), 2)

    def test_unbalanced(self):
        with self.assertRaises(kishot.Failure):
            kishot.closing_paren("(a (b)", 0)

    def test_strip_fills_keeps_outlines(self):
        out = kishot.strip_fills(BOARD)
        self.assertNotIn("filled_polygon", out)
        self.assertEqual(out.count("(polygon"), BOARD.count("(polygon"))
        self.assertEqual(out.count("("), out.count(")"))
        self.assertEqual(len(kishot.top_level_items(out)), len(self.items))

    def test_title_block(self):
        tb = kishot.title_block(BOARD)
        self.assertEqual(tb, {"title": "Demo (board)", "rev": "B", "date": "", "company": ""})

    def test_outline(self):
        self.assertEqual(kishot.outline_box(self.items), (10, 20, 60, 50))

    def test_copper_layers_in_stack_order(self):
        self.assertEqual(kishot.copper_layers(self.items),
                         [("F.Cu", ""), ("In1.Cu", "GND"), ("In2.Cu", ""), ("B.Cu", "")])

    def test_planes(self):
        layers = kishot.copper_layers(self.items)
        found = kishot.planes(self.items, layers, kishot.outline_box(self.items))
        # The small +3V3 pour is not a plane, and the keepout has no net.
        self.assertEqual(found, {"F.Cu": ["GND"], "In1.Cu": [], "In2.Cu": [], "B.Cu": ["GND"]})

    def test_read_board(self):
        with tempfile.TemporaryDirectory() as t:
            pcb = Path(t) / "demo.kicad_pcb"
            pcb.write_text(BOARD, encoding="utf-8")
            b = kishot.read_board(pcb)
        self.assertEqual(b["tracks"], {"F.Cu": 2, "In1.Cu": 0, "In2.Cu": 0, "B.Cu": 1})
        self.assertEqual(b["footprints"], 2)

    def test_schematic_title_when_the_board_has_none(self):
        with tempfile.TemporaryDirectory() as t:
            pcb = Path(t) / "demo.kicad_pcb"
            pcb.write_text(BOARD.replace('(title "Demo (board)")', ""), encoding="utf-8")
            pcb.with_suffix(".kicad_sch").write_text('(kicad_sch (title_block (title "From sch")))')
            self.assertEqual(kishot.read_board(pcb)["title_block"]["title"], "From sch")


class Captions(unittest.TestCase):
    def test_layer_caption(self):
        self.assertEqual(kishot.layer_caption("In1.Cu", "GND", 0, ["GND"]),
                         "In1.Cu (GND)  ·  0 tracks  ·  plane GND")
        self.assertEqual(kishot.layer_caption("B.Cu", "", 1, ["GND", "+5V"]),
                         "B.Cu, seen from below  ·  1 track  ·  planes GND, +5V")

    def test_lua_string_cannot_be_closed_early(self):
        self.assertEqual(kishot.lua_string("a]]b"), "[=[a]]b]=]")


class CommandLine(unittest.TestCase):
    def test_find_board(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            with self.assertRaisesRegex(kishot.Failure, "no .kicad_pcb"):
                kishot.find_board(d)
            (d / "a.kicad_pcb").write_text(BOARD)
            (d / "_autosave-a.kicad_pcb").write_text(BOARD)
            self.assertEqual(kishot.find_board(d).name, "a.kicad_pcb")
            (d / "b.kicad_pcb").write_text(BOARD)
            with self.assertRaisesRegex(kishot.Failure, "2 .kicad_pcb files"):
                kishot.find_board(d)

    def test_default_out_dir(self):
        with tempfile.TemporaryDirectory() as t:
            pcb = Path(t) / "a.kicad_pcb"
            self.assertEqual(kishot.default_out_dir(pcb), Path(t))
            (Path(t) / "fabrication").mkdir()
            self.assertEqual(kishot.default_out_dir(pcb), Path(t) / "fabrication")

    def test_sheets(self):
        self.assertEqual(kishot.parse_args(["-s", "layers"]).sheet, ["layers"])
        with self.assertRaises(SystemExit):
            kishot.parse_args(["-s", "photo"])


if __name__ == "__main__":
    unittest.main()
