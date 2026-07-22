"""Furrifier version: major.minor.build.

`build` (the third component) auto-increments on every exe build — the
`furrify_fo4.spec` preamble bumps it before PyInstaller runs. major/minor are
edited by hand. The version is stamped into each generated plugin's TES4 author
(see session.run) so a patch records exactly which build produced it.
"""

__version__ = "1.1.1"
