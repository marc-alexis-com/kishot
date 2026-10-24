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


class Diff(unittest.TestCase):
    def test_track_keys_ignore_ids_and_nets(self):
        moved = BOARD.replace("(end 12 22)\n\t\t(layer \"F.Cu\")", "(end 14 22)\n\t\t(layer \"F.Cu\")", 1)
        old, _ = kishot.track_keys(kishot.top_level_items(BOARD))
        new, _ = kishot.track_keys(kishot.top_level_items(moved.replace("(net_name", "(uuid \"x\") (net_name")))
        self.assertEqual(len(new["F.Cu"] - old["F.Cu"]), 1)
        self.assertEqual(len(old["F.Cu"] - new["F.Cu"]), 0)  # the two identical segments are one
        self.assertEqual(new["B.Cu"], old["B.Cu"])

    def test_vias(self):
        via = '\t(via\n\t\t(at 20 30)\n\t\t(size 0.6)\n\t\t(drill 0.3)\n\t\t(layers "F.Cu" "B.Cu")\n\t\t(net 1)\n\t)\n)\n'
        board = BOARD[:BOARD.rindex(")")] + via
        _, vias = kishot.track_keys(kishot.top_level_items(board))
        _, moved = kishot.track_keys(kishot.top_level_items(board.replace("(at 20 30)", "(at 21 30)")))
        self.assertEqual(len(vias), 1)
        self.assertEqual(len(vias - moved), 1)

    def test_crop_svg(self):
        with tempfile.TemporaryDirectory() as t:
            svg = Path(t) / "a.svg"
            svg.write_text('<svg width="297.0022mm" height="210.0072mm" viewBox="0.0000 0.0000 297.0022 210.0072">')
            kishot.crop_svg(svg, (10, 20, 60, 50))
            self.assertIn('width="50.0000mm" height="30.0000mm" viewBox="10.0000 20.0000 50.0000 30.0000"',
                          svg.read_text())
            svg.write_text("<svg>")
            with self.assertRaises(kishot.Failure):
                kishot.crop_svg(svg, (0, 0, 1, 1))

    def test_diff_sheet_needs_a_revision(self):
        self.assertEqual(kishot.main(["-s", "diff"]), 2)
        self.assertEqual(kishot.main(["-s", "net"]), 2)


NETS_BOARD = """(kicad_pcb
\t(net 0 "")
\t(net 1 "GND")
\t(net 2 "/power/VBAT")
\t(net 3 "/io/VBAT_SENSE")
\t(segment (start 0 0) (end 3 4) (width 0.2) (layer "F.Cu") (net 1))
\t(segment (start 0 0) (end 6 8) (width 0.3) (layer "F.Cu") (net 2))
\t(arc (start 1 0) (mid 0 1) (end -1 0) (width 0.2) (layer "B.Cu") (net 2))
\t(via (at 1 1) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 1))
\t(zone (net 1) (net_name "GND") (layer "B.Cu") (polygon (pts (xy 0 0) (xy 1 0) (xy 1 1)))
\t\t(filled_polygon (layer "B.Cu") (pts (xy 0 0) (xy 1 0) (xy 1 1))))
\t(footprint "R" (layer "F.Cu")
\t\t(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "GND"))
\t\t(pad "2" smd rect (at 2 0) (size 1 1) (layers "F.Cu") (net 2 "/power/VBAT")))
\t(gr_line (start 0 0) (end 9 9) (layer "F.Cu"))
\t(gr_rect (start 0 0) (end 10 10) (layer "Edge.Cuts"))
)
"""


class Nets(unittest.TestCase):
    def setUp(self):
        self.items = kishot.top_level_items(NETS_BOARD)
        self.netmap = kishot.net_table(self.items)
        self.board = {"netmap": self.netmap, "text": NETS_BOARD}

    def test_item_net_by_number_and_by_name(self):
        seg = [t for h, t in self.items if h == "segment"][1]
        self.assertEqual(kishot.item_net(seg, self.netmap), "/power/VBAT")
        self.assertEqual(kishot.item_net('(pad "1" (net "GND"))', {}), "GND")  # KiCad 10
        self.assertEqual(kishot.item_net('(segment (net 7))', {}), "")

    def test_find_net(self):
        self.assertEqual(kishot.find_net(self.board, "GND"), "GND")
        self.assertEqual(kishot.find_net(self.board, "gnd"), "GND")
        self.assertEqual(kishot.find_net(self.board, "vbat"), "/power/VBAT")
        with self.assertRaisesRegex(kishot.Failure, "close: .*VBAT"):
            kishot.find_net(self.board, "VBATT")

    def test_only_net(self):
        out = kishot.only_net(NETS_BOARD, self.netmap, "/power/VBAT")
        heads = [h for h, _ in kishot.top_level_items(out)]
        self.assertEqual(heads.count("segment"), 1)
        self.assertEqual(heads.count("arc"), 1)
        self.assertNotIn("via", heads)
        self.assertNotIn("zone", heads)
        self.assertNotIn("gr_line", heads)  # graphic copper is not the net's
        self.assertIn("gr_rect", heads)  # the outline stays
        footprint = next(t for h, t in kishot.top_level_items(out) if h == "footprint")
        self.assertNotIn('"GND"', footprint)
        self.assertIn("/power/VBAT", footprint)

    def test_track_length(self):
        segs = [(h, t) for h, t in self.items if h in ("segment", "arc")]
        self.assertAlmostEqual(kishot.track_length(*segs[0]), 5.0)
        self.assertAlmostEqual(kishot.track_length(*segs[2]), 3.14159265, places=5)


class Sheets(unittest.TestCase):
    def pick(self, *argv):
        return kishot.choose_sheets(kishot.parse_args(list(argv)))

    def test_defaults(self):
        self.assertEqual(self.pick(), ["3d", "layers"])
        self.assertEqual(self.pick("--net", "GND"), ["net"])
        self.assertEqual(self.pick("--diff", "HEAD~1"), ["diff", "sch-diff", "3d-diff", "fade"])
        self.assertEqual(self.pick("--diff", "HEAD", "-s", "fade"), ["fade"])

    def test_all(self):
        self.assertEqual(self.pick("-s", "all"), ["3d", "layers", "xray", "silk", "assembly", "bom", "dims",
                                                  "stackup", "gerbers", "print", "spin"])
        self.assertEqual(self.pick("-s", "all", "--net", "GND", "--diff", "HEAD"),
                         list(kishot.SHEETS))
        self.assertNotIn("gerbers", kishot.choose_sheets(kishot.parse_args(["-s", "all"]), gerbv=False))

    def test_missing_option(self):
        self.assertIn("--diff", self.pick("-s", "3d-diff"))
        self.assertIn("--net", self.pick("-s", "net"))


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


PARTS = """(kicad_pcb
\t(setup
\t\t(stackup
\t\t\t(layer "F.SilkS" (type "Top Silk Screen") (color "White"))
\t\t\t(layer "F.Mask" (type "Top Solder Mask") (color "Blue") (thickness 0.01))
\t\t\t(layer "F.Cu" (type "copper") (thickness 0.035))
\t\t\t(layer "dielectric 1" (type "core") (thickness 1.51 locked) (material "FR4") (epsilon_r 4.5))
\t\t\t(layer "B.Cu" (type "copper") (thickness 0.035))
\t\t\t(copper_finish "ENIG")
\t\t)
\t)
\t(footprint "Resistor_SMD:R_0603"
\t\t(layer "F.Cu")
\t\t(at 20 30 90)
\t\t(property "Reference" "R2" (at 0 -1 90) (layer "F.SilkS"))
\t\t(property "Value" "10k" (at 0 1 90) (layer "F.Fab"))
\t\t(attr smd)
\t)
\t(footprint "Resistor_SMD:R_0603"
\t\t(layer "B.Cu")
\t\t(at 25 30)
\t\t(property "Reference" "R1" (at 0 -1 0) (layer "B.SilkS"))
\t\t(property "Value" "10k" (at 0 1 0) (layer "B.Fab") (hide yes))
\t\t(attr smd)
\t)
\t(footprint "Resistor_SMD:R_0603"
\t\t(layer "F.Cu")
\t\t(at 30 30)
\t\t(property "Reference" "R3" (at 0 -1 0) (layer "F.SilkS"))
\t\t(property "Value" "10k" (at 0 1 0) (layer "F.Fab"))
\t\t(attr smd dnp)
\t)
\t(footprint "Logo:Logo"
\t\t(layer "F.Cu")
\t\t(at 40 40)
\t\t(property "Reference" "#G1" (at 0 0 0) (layer "F.SilkS"))
\t\t(attr exclude_from_bom)
\t)
\t(footprint "MountingHole:MountingHole_3.2mm"
\t\t(layer "F.Cu")
\t\t(at 50 45 90)
\t\t(property "Reference" "H1" (at 0 0 0) (layer "F.SilkS"))
\t\t(pad "" np_thru_hole circle (at 1 0) (size 3.2 3.2) (drill 3.2) (layers "*.Cu" "*.Mask"))
\t\t(attr exclude_from_pos_files exclude_from_bom)
\t)
\t(footprint "Connector:USB_C"
\t\t(layer "F.Cu")
\t\t(at 12 25)
\t\t(property "Reference" "J1" (at 0 0 0) (layer "F.SilkS"))
\t\t(property "Value" "USB_C" (at 0 0 0) (layer "F.Fab"))
\t\t(pad "" np_thru_hole circle (at 0 0) (size 0.65 0.65) (drill 0.65) (layers "*.Cu" "*.Mask"))
\t\t(attr smd)
\t)
)
"""


class Parts(unittest.TestCase):
    def setUp(self):
        self.board = {"text": PARTS, "items": kishot.top_level_items(PARTS)}
        self.parts = kishot.footprints(self.board["items"])

    def test_footprints(self):
        r2 = self.parts[0]
        self.assertEqual((r2["ref"], r2["value"], r2["footprint"], r2["x"], r2["y"], r2["rot"], r2["side"]),
                         ("R2", "10k", "R_0603", 20.0, 30.0, 90.0, "top"))
        self.assertEqual(self.parts[1]["side"], "bottom")
        self.assertTrue(self.parts[2]["dnp"])
        self.assertFalse(self.parts[3]["bom"])

    def test_bom_groups(self):
        fitted, dnp = kishot.bom_groups(self.parts)
        self.assertEqual([(v, fp, [f["ref"] for f in fs]) for v, fp, fs in fitted],
                         [("USB_C", "USB_C", ["J1"]), ("10k", "R_0603", ["R2", "R1"])])
        self.assertEqual([[f["ref"] for f in fs] for _, _, fs in dnp], [["R3"]])

    def test_ref_ranges(self):
        self.assertEqual(kishot.ref_ranges(["R3", "R1", "R2", "R7", "R10", "C1"]), "C1, R1–R3, R7, R10")
        self.assertEqual(kishot.ref_ranges(["R1", "R2"]), "R1, R2")
        self.assertEqual(kishot.ref_ranges(["J1", "SW_A"]), "J1, SW_A")

    def test_mounting_holes(self):
        # The hole is at (1, 0) in a footprint turned by 90°: KiCad's y points down.
        (x, y, d), = kishot.mounting_holes(self.board)
        self.assertAlmostEqual(x, 50.0, places=6)
        self.assertAlmostEqual(y, 44.0, places=6)
        self.assertEqual(d, 3.2)

    def test_hide_values(self):
        out = kishot.hide_values(PARTS)
        self.assertEqual(out.count("(hide yes)"), 4)
        self.assertEqual(out.count("("), out.count(")"))

    def test_stackup(self):
        layers, finish = kishot.stackup(self.board)
        self.assertEqual(finish, "ENIG")
        self.assertEqual([x["name"] for x in layers], ["F.SilkS", "F.Mask", "F.Cu", "dielectric 1", "B.Cu"])
        self.assertEqual(layers[3]["thickness"], 1.51)
        self.assertEqual(layers[3]["material"], "FR4")
        self.assertAlmostEqual(kishot.stackup_thickness(self.board), 1.59)
        self.assertEqual(kishot.layer_fill(layers[1]), kishot.NAMED_COLOURS["blue"])


class Spin(unittest.TestCase):
    def test_turn_and_swing(self):
        self.assertEqual(kishot.spin_angle(24, 96, 0), 90)
        self.assertAlmostEqual(kishot.spin_angle(24, 96, 30), 30)
        self.assertAlmostEqual(kishot.spin_angle(72, 96, 30), -30)


class Config(unittest.TestCase):
    def test_find_and_load(self):
        if sys.version_info < (3, 11):
            self.skipTest("tomllib")
        with tempfile.TemporaryDirectory() as t:
            top = Path(t)
            (top / ".git").mkdir()
            (top / "hw").mkdir()
            pcb = top / "hw" / "a.kicad_pcb"
            self.assertIsNone(kishot.find_config(pcb))
            (top / ".kishot.toml").write_text('sheets = "bom"\nout_dir = "fab"\nworkspace = 12\nseconds = 3\n')
            cfg = kishot.load_config(kishot.find_config(pcb))
            self.assertEqual(cfg, {"config_sheets": ["bom"], "out_dir": (top / "fab").resolve(),
                                   "workspace": "12", "seconds": 3.0})
            a = kishot.parse_args([], cfg)
            self.assertEqual(kishot.choose_sheets(a), ["bom"])
            self.assertEqual(kishot.choose_sheets(kishot.parse_args(["-s", "3d"], cfg)), ["3d"])
            (top / ".kishot.toml").write_text("drc = 1\n")
            with self.assertRaisesRegex(kishot.Failure, "drc should be a bool"):
                kishot.load_config(top / ".kishot.toml")


class Batches(unittest.TestCase):
    def test_recursive(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            for f in ("a/x.kicad_pcb", "b/y.kicad_pcb", "b/y-backups/y.kicad_pcb", ".h/z.kicad_pcb",
                      "a/_autosave-x.kicad_pcb"):
                (d / f).parent.mkdir(parents=True, exist_ok=True)
                (d / f).write_text(BOARD)
            self.assertEqual([p.name for p in kishot.find_boards([d], True)], ["x.kicad_pcb", "y.kicad_pcb"])
            with self.assertRaisesRegex(kishot.Failure, "no .kicad_pcb"):
                kishot.find_boards([d], False)


if __name__ == "__main__":
    unittest.main()
