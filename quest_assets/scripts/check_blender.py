import bpy
print('BLENDER_READY', bpy.app.version_string, flush=True)
print('RENDER_ENGINES', [(item.identifier, item.name) for item in bpy.types.RenderSettings.bl_rna.properties['engine'].enum_items], flush=True)
