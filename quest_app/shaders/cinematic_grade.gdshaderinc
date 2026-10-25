// The cinematic look, shared by the XR cinema panel and the desktop cut: crushed blacks,
// more contrast, amber highlights over neutral shadows, and a soft vignette. Works on
// display-referred (sRGB) colour, so both paths grade the same thing.

vec3 cine_grade(vec3 c, vec2 frame_uv) {
	c = max(c - vec3(0.03), vec3(0.0)) / 0.97;
	float l = dot(c, vec3(0.299, 0.587, 0.114));
	// the room is already warm: pull saturation back a little, then add contrast, keep the
	// shadows neutral and give only the highlights the amber push
	c = mix(vec3(l), c, 0.85);
	c = (c - 0.40) * 1.28 + 0.40;
	c *= mix(vec3(0.97, 0.98, 1.02), vec3(1.06, 1.0, 0.88), smoothstep(0.1, 0.8, l));
	vec2 d = (frame_uv - 0.5) * 2.0;
	c *= 1.0 - 0.55 * smoothstep(0.55, 1.45, length(d));
	return clamp(c, 0.0, 1.0);
}

vec3 cine_srgb_to_linear(vec3 c) {
	return mix(c / 12.92, pow((c + 0.055) / 1.055, vec3(2.4)), step(vec3(0.04045), c));
}
