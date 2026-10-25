class_name TestCase
extends RefCounted
## Minimal assertions for the headless runner (tests/run_tests.gd).

var failures: Array = []
var current := ""
var tree: SceneTree


func check(cond: bool, what: String) -> bool:
	if not cond:
		failures.append("%s: %s" % [current, what])
	return cond


func check_eq(a, b, what: String) -> bool:
	return check(a == b, "%s (got %s, want %s)" % [what, a, b])


func check_near(a: float, b: float, tol: float, what: String) -> bool:
	return check(abs(a - b) <= tol, "%s (got %.4f, want %.4f +- %.4f)" % [what, a, b, tol])


## Adds a node to the running SceneTree so AnimationTrees and attachments resolve.
func mount(n: Node) -> Node:
	tree.root.add_child(n)
	return n
