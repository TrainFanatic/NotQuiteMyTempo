shader_type spatial;
// The "kit window". Fades the rendered frame out where the real drum kit is, so the
// headset's passthrough image (composited under the app's frame) shows the real kit.
//
// Shape. The window is a dome over the kit, an ellipsoid centred on the floor, which is this
// mesh's local unit sphere (the mesh is scaled by the dome's radii). Each pixel's view ray is
// tested against it analytically. The closest the ray comes to the dome's centre, in units
// of the radii (rho, 0 at the centre, 1 on the surface), sets how much reality shows. It is
// full below feather_start and fades out to nothing at rho = 1, so the window is a soft blob
// around the drums, not a hard-edged outline.
//
// How it punches. blend_mul with colour and alpha both (1 - hole) scales the premultiplied
// frame toward rgba 0, which the Quest compositor shows as passthrough. It draws in the
// transparent pass, after all opaque geometry, depth tested against the opaque depth. The
// depth it writes is not the mesh's own but the dome's near surface along the view ray (the
// analytic ray-ellipsoid entry point; rays in the feather band that just miss the dome use
// their closest approach to it). So only virtual things between the eye and the dome, the
// part of the room in front of the kit, keep their pixels (the window fails the depth test
// there). Anything beyond the dome surface, the room shell and also the band or Terence
// standing behind the kit, is farther than the written depth, so the window passes and fades
// it out, and the real kit hides it. The depth is
// clamped to where the ray leaves the room's interior box (room_min..room_max, already pulled
// in from the walls and up from the floor), so a ray that only reaches the dome's buried
// lower half, under the floor, still leaves the floor next to the kit alone.
//
// The mesh only decides where on screen the window can be. Only its back faces are drawn,
// so each pixel is covered once and the near plane can cut into it safely.
render_mode unshaded, blend_mul, depth_draw_never, cull_front, shadows_disabled, fog_disabled;

uniform vec3 room_min;
uniform vec3 room_max;
uniform float feather_start = 0.7;

// closest a written depth may be to the eye, so the projection never divides by zero (metres)
const float MIN_DEPTH_T = 0.01;

void fragment() {
	// this eye's position (EYE_OFFSET is the per-eye offset in multiview, zero otherwise)
	vec3 eye = (INV_VIEW_MATRIX * vec4(EYE_OFFSET, 1.0)).xyz;
	vec3 surface = (INV_VIEW_MATRIX * vec4(VERTEX, 1.0)).xyz;
	vec3 dir = normalize(surface - eye);
	// slab test: the nearest of the three far planes is where the ray leaves the room
	vec3 inv = 1.0 / dir;
	vec3 t_far = max((room_min - eye) * inv, (room_max - eye) * inv);
	float t_exit = min(min(t_far.x, t_far.y), t_far.z);

	// the ray in the dome's unit-sphere space (the mesh's local frame); t stays in metres
	mat4 to_local = inverse(MODEL_MATRIX);
	vec3 e = (to_local * vec4(eye, 1.0)).xyz;
	vec3 d = mat3(to_local) * dir;
	float a = dot(d, d);
	float b = dot(e, d);
	float t_close = -b / a;
	float rho = length(e + d * clamp(t_close, 0.0, t_exit));
	float hole = 1.0 - smoothstep(feather_start, 1.0, rho);
	if (hole <= 0.0) {
		discard;
	}
	ALBEDO = vec3(1.0 - hole);
	ALPHA = 1.0 - hole;

	// where the ray enters the dome: |e + d t| = 1, nearer root. A miss (only possible in
	// the feather band) uses the closest approach instead.
	float disc = b * b - a * (dot(e, e) - 1.0);
	float t_entry = disc >= 0.0 ? (-b - sqrt(disc)) / a : t_close;
	float t_depth = clamp(t_entry, MIN_DEPTH_T, t_exit);
	vec3 entry_view = (VIEW_MATRIX * vec4(eye + dir * t_depth, 1.0)).xyz;
	vec4 clip = PROJECTION_MATRIX * vec4(entry_view, 1.0);
	DEPTH = clip.z / clip.w;
}
