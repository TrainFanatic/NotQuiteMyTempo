extends TestCase
## C1: every imported glb passes the import validator.


func test_all_assets_valid() -> void:
	var paths := ImportValidator.asset_paths()
	check_eq(paths.size(), 41, "glb count under res://assets")
	var meta: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(Terence.MOVES_JSON))
	var problems := ImportValidator.validate(paths, meta["moves"].keys())
	for p in problems:
		check(false, p)


func test_validator_catches_problems() -> void:
	# a band glb checked against a move list it can't have must fail
	var p := ImportValidator.validate_one("res://assets/band/band_trumpet_1.glb", [])
	check(p.is_empty(), "band trumpet passes on its own")
	var bad := ImportValidator.validate_one("res://assets/terence/terence_v003.glb", ["not_a_move"])
	check(bad.size() == 1 and String(bad[0]).contains("not_a_move"), "missing animation is reported")
