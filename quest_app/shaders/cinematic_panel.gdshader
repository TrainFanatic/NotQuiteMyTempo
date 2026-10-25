shader_type spatial;
// The XR cinema panel: the cinematic camera's SubViewport, graded, between black letterbox
// bars. Drawn over everything (it is placed clear of Terence and the drums), unlit, with
// nearest filtering to keep the PS2 look. `fade` takes it out after the last shot.
render_mode unshaded, blend_mix, depth_test_disabled, depth_draw_never, cull_back, shadows_disabled, fog_disabled;

#include "res://shaders/cinematic_grade.gdshaderinc"

uniform sampler2D frame_tex : filter_nearest;
// height of each letterbox bar as a fraction of the panel's height
uniform float bar = 0.09;
uniform float fade : hint_range(0.0, 1.0) = 1.0;

void fragment() {
	float v = (UV.y - bar) / (1.0 - 2.0 * bar);
	vec3 c = vec3(0.0);
	if (v >= 0.0 && v <= 1.0) {
		vec2 uv = vec2(UV.x, v);
		c = cine_grade(texture(frame_tex, uv).rgb, uv);
	}
	// the viewport texture holds display colour; the scene's output expects linear
	ALBEDO = cine_srgb_to_linear(c);
	ALPHA = fade;
}
