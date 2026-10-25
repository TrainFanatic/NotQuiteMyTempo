// The cinematic look for the desktop cut: more contrast, amber highlights over neutral
// shadows, and a soft vignette. Works on display-referred (sRGB) colour.

vec3 cine_grade(vec3 c, vec2 frame_uv) {
	// The room is already warm and dark, so contrast works on luminance (an S-curve pinned
	// at black and white) and keeps each pixel's hue: a per-channel curve would clip the
	// green and blue out of the shadows and turn them red.
	float l = max(dot(c, vec3(0.299, 0.587, 0.114)), 1e-4);
	float s = l * l * (3.0 - 2.0 * l);
	float nl = mix(l, s, 0.6) * 1.08;
	c *= nl / l;
	// a touch less saturation, then amber in the highlights only
	c = mix(vec3(nl), c, 0.9);
	c *= mix(vec3(1.0), vec3(1.06, 1.0, 0.9), smoothstep(0.25, 0.85, nl));
	vec2 d = (frame_uv - 0.5) * 2.0;
	c *= 1.0 - 0.5 * smoothstep(0.6, 1.45, length(d));
	return clamp(c, 0.0, 1.0);
}
