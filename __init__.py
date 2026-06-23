"""QGIS plugin entry point for Section Georeferencer."""


def classFactory(iface):
    from .section_georeferencer_plugin import SectionGeoreferencerPlugin
    return SectionGeoreferencerPlugin(iface)
