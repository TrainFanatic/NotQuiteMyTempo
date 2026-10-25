class_name StateMachineBuilder
extends RefCounted
## Shared character constants. (The name is historical: the band and Terence used to be
## AnimationTree state machines built here; they now crossfade with AnimBlender.)

## Offset for anything parented under a BoneAttachment3D on the Instrument/Prop bones.
## Godot keeps Blender's bone axes on imported joints (bone Y along the bone, Blender Z up)
## while the attached glbs are converted to Godot's Y-up, so the glb needs Blender's
## axis conversion undone: +90 degrees about X.
const ATTACH_OFFSET := Transform3D(Basis(Vector3.RIGHT, PI / 2.0), Vector3.ZERO)
