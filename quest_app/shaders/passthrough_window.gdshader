shader_type spatial;
// Punches a hole in the rendered frame so the headset's passthrough image shows through:
// blend_mul multiplies the colour and alpha already drawn there by 0, leaving alpha 0.
// Used for the "kit window" around the real drum kit in XR mode.
render_mode unshaded, blend_mul, cull_disabled, depth_draw_never, shadows_disabled;

void fragment() {
	ALBEDO = vec3(0.0);
	ALPHA = 0.0;
}
