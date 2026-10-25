shader_type canvas_item;
// The desktop cut: grades the cinematic camera's frame on screen and masks it to 2.39:1
// with black bars.

#include "res://shaders/cinematic_grade.gdshaderinc"

uniform sampler2D screen_tex : hint_screen_texture, filter_nearest;
uniform float aspect = 2.39;

void fragment() {
	vec2 size = 1.0 / SCREEN_PIXEL_SIZE;
	float frame_h = min(1.0, size.x / aspect / size.y);
	float bar = (1.0 - frame_h) / 2.0;
	float v = (SCREEN_UV.y - bar) / frame_h;
	vec3 c = vec3(0.0);
	if (v >= 0.0 && v <= 1.0) {
		c = cine_grade(texture(screen_tex, SCREEN_UV).rgb, vec2(SCREEN_UV.x, v));
	}
	COLOR = vec4(c, 1.0);
}
