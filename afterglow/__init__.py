from . import props, ui


def register():
    props.register()
    ui.register()


def unregister():
    ui.unregister()
    props.unregister()
