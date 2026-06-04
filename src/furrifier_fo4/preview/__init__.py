"""Live-preview backend + Qt pane for the FO4 furrifier GUI.

`PreviewSession` loads the plugin set once (~15s) and bakes one NPC on demand
(furrify + assemble facegeom + composite texture into a temp dir) so the GUI's
3D viewport can show a furrified head before committing to a full run.
"""

from .session import PreviewSession  # noqa: F401
