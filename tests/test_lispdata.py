from civil3d_mcp.lispdata import parse


def test_layers_shape():
    rows = parse('(("0" 7 0) ("A-WALL" -1 1) ("Kerb \\"new\\"" 3 4))')
    assert rows == [["0", 7, 0], ["A-WALL", -1, 1], ['Kerb "new"', 3, 4]]


def test_inventory_shape():
    assert parse('(2 ("Main Rd" "Access"))') == [2, ["Main Rd", "Access"]]
    assert parse("(0 nil)") == [0, None]


def test_atoms_and_dotted():
    assert parse("12.5") == 12.5
    assert parse("(8 . \"WALL\")") == [8, "WALL"]
    assert parse("T") is True
