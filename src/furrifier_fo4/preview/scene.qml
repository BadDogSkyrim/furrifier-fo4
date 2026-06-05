import QtQuick
import QtQuick3D

// Embedded scene for the live preview pane. Loaded by a QQuickWidget,
// so the root is an Item (not a Window). The `previewCtx` context
// property is set from Python before load; it exposes the shape list,
// the scene bounds, and light/camera defaults.
Item {
    id: root
    anchors.fill: parent

    // Spherical camera state driven by the MouseArea below.
    property real yaw: 0
    property real pitch: 0
    // Null-safe views of the context properties: on teardown previewCtx
    // becomes null while these bindings still re-evaluate, which logged
    // "Cannot read property '…' of null" on exit. Read through these.
    property real ctxRadius: previewCtx ? previewCtx.radius : 1
    property var ctxCenter: previewCtx ? previewCtx.center : Qt.vector3d(0, 0, 0)
    // Framed to the scene bounds whenever the radius changes
    // (which it does on every new shape set — see FacegenSceneWidget).
    property real distance: ctxRadius * 2.5
    property real radius: ctxRadius
    // Pan offset in scene units. Translates the orbit pivot in the
    // screen plane; reset whenever a new NPC is loaded so the view
    // re-centers on the new head.
    property real panX: 0
    property real panY: 0

    onRadiusChanged: {
        distance = radius * 2.5
        panX = 0
        panY = 0
    }

    View3D {
        id: view
        anchors.fill: parent

        environment: SceneEnvironment {
            clearColor: "#2b3644"   // dark, unsaturated blue
            backgroundMode: SceneEnvironment.Color
            antialiasingMode: SceneEnvironment.MSAA
            antialiasingQuality: SceneEnvironment.High
        }

        // Camera orbits around `pivotNode`, which itself gets
        // translated by (panX, panY) in camera-local coordinates
        // so panning feels screen-aligned regardless of orbit angle.
        Node {
            id: pivotNode
            position: Qt.vector3d(
                root.ctxCenter.x
                    - root.panX * Math.cos(root.yaw)
                    - root.panY * Math.sin(root.yaw) * Math.sin(root.pitch),
                root.ctxCenter.y + root.panY * Math.cos(root.pitch),
                root.ctxCenter.z
                    + root.panX * Math.sin(root.yaw)
                    - root.panY * Math.cos(root.yaw) * Math.sin(root.pitch))
        }

        PerspectiveCamera {
            id: camera
            position: Qt.vector3d(
                pivotNode.position.x + root.distance * Math.sin(root.yaw) * Math.cos(root.pitch),
                pivotNode.position.y + root.distance * Math.sin(root.pitch),
                pivotNode.position.z + root.distance * Math.cos(root.yaw) * Math.cos(root.pitch))
            eulerRotation: Qt.vector3d(
                -root.pitch * 180 / Math.PI,
                root.yaw * 180 / Math.PI,
                0)
            clipNear: 0.1
            clipFar: Math.max(root.ctxRadius * 20, 100)
        }

        // Three-point lighting (key + fill + bounce) + a rim light
        // behind the head. All directions have a -Z component so they
        // hit the +Z-facing head; the rim's 180° yaw flips it to +Z.
        DirectionalLight {
            eulerRotation: Qt.vector3d(-20, 25, 0)
            brightness: 1.0
        }
        DirectionalLight {
            eulerRotation: Qt.vector3d(-10, -30, 0)
            brightness: 0.4
        }
        DirectionalLight {
            eulerRotation: Qt.vector3d(20, 0, 0)
            brightness: 0.2
        }
        DirectionalLight {
            eulerRotation: Qt.vector3d(-15, 180, 0)
            brightness: 0.25
        }

        // Skyrim NIF: X-right, Y-forward, Z-up. Qt Quick 3D: X-right,
        // Y-up, -Z-camera-view. No single axis-aligned rotation maps
        // this without also mirroring, so nest two: inner yaw 180°,
        // outer pitch 90°. See Phase 0 findings in
        // PLAN_FURRIFIER_PREVIEW.md.
        Node {
            id: sceneRootPitch
            eulerRotation.x: 90

            Node {
                id: sceneRootYaw
                eulerRotation.y: 180

                Repeater3D {
                    model: previewCtx ? previewCtx.shapes : []
                    delegate: Model {
                        geometry: modelData.geometry
                        materials: PrincipledMaterial {
                            // Skyrim Skin_Tint(5) shapes carry a per-shape
                            // tint baked into the nif (e.g. BDMino horn
                            // base — issue #11). Non-Skin_Tint shapes get
                            // neutral white so the diffuse passes through.
                            baseColor: modelData.baseColor
                            baseColorMap: Texture {
                                source: modelData.diffuseUrl
                                minFilter: Texture.Linear
                                magFilter: Texture.Linear
                                mipFilter: Texture.Linear
                                generateMipmaps: true
                            }
                            roughness: 0.85
                            metalness: 0.0
                            // Per-shape alpha from the nif's NiAlphaProperty:
                            // Blend for hair / scars / hairlines, Mask for
                            // head / mouth / eyebrow cards, Default for
                            // opaque shapes. Python side already mapped
                            // NIF flags to the string; fall back to Mask if
                            // the property is unset.
                            alphaMode: modelData.alphaMode === "Blend"
                                ? PrincipledMaterial.Blend
                                : modelData.alphaMode === "Default"
                                    ? PrincipledMaterial.Default
                                    : PrincipledMaterial.Mask
                            alphaCutoff: modelData.alphaCutoff
                        }
                    }
                }
            }
        }
    }

    // Manual orbit controller — OrbitCameraController didn't respond
    // to drag reliably during Phase 0; MouseArea works everywhere.
    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
        property real lastX: 0
        property real lastY: 0
        onPressed: function(mouse) {
            lastX = mouse.x
            lastY = mouse.y
        }
        onPositionChanged: function(mouse) {
            var dx = mouse.x - lastX
            var dy = mouse.y - lastY
            // Middle-drag OR Shift+Left-drag → pan. Pan speed scales
            // with scene size so it feels the same on close-up and
            // zoomed-out views.
            var panning = (mouse.buttons & Qt.MiddleButton)
                || ((mouse.buttons & Qt.LeftButton)
                    && (mouse.modifiers & Qt.ShiftModifier))
            if (panning) {
                var panScale = root.distance * 0.0015
                root.panX += dx * panScale
                root.panY += dy * panScale
            } else if (mouse.buttons & Qt.LeftButton) {
                // Drag-right rotates the scene right (camera yaws
                // left — hence the negated delta).
                root.yaw -= dx * 0.008
                root.pitch += dy * 0.008
                if (root.pitch > 1.5) root.pitch = 1.5
                if (root.pitch < -1.5) root.pitch = -1.5
            }
            lastX = mouse.x
            lastY = mouse.y
        }
        onWheel: function(wheel) {
            root.distance *= wheel.angleDelta.y > 0 ? 0.9 : 1.1
        }
    }
}
