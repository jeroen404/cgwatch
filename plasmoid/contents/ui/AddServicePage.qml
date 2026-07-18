import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.components as PlasmaComponents
import org.kde.kirigami as Kirigami
import "logic.js" as Logic

// Add-service flow (plan §2/§4, mirrors tui.py's AddServiceModal): a
// candidate picker (running-but-unlimited services from the last dump's
// `candidates`, main.qml property) plus manual Unit/MemoryMax/CPUQuota
// entry. All validation lives in cgwatch-cli -- this page only surfaces
// its `messages` verbatim on failure, it never second-guesses the input.
Item {
    id: addPage

    property var rootItem

    property string localError: ""

    readonly property bool busy: rootItem.actionInFlight
                                  && rootItem.pendingActionKey === rootItem.addPageContextKey

    // TUI parity (AddServiceModal.compose): collapse a template instance's
    // UUID for display, e.g. "app-foo@1234abcd.service" -> "app-foo@….service"
    function collapseUnit(name) {
        var n = String(name || "")
        var at = n.indexOf("@")
        if (at !== -1 && n.slice(-8) === ".service")
            return n.slice(0, at + 1) + "….service"
        return n
    }

    function reset() {
        unitField.text = ""
        memField.text = ""
        cpuField.text = ""
        // Failure-survives-popup-close: replay an unconsumed failed result
        // for this page (the popup was closed/reopened before the
        // Connections handler below ever got to show it) instead of
        // unconditionally clearing localError.
        var hint = Logic.consumeActionError(rootItem, rootItem.addPageContextKey)
        localError = hint !== null ? hint : ""
    }

    // fresh fields every time the page is (re)opened
    onVisibleChanged: if (visible) reset()

    Connections {
        target: rootItem
        function onActionResultSeqChanged() {
            var hint = Logic.consumeActionError(rootItem, rootItem.addPageContextKey)
            if (hint !== null)
                addPage.localError = hint
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: Kirigami.Units.smallSpacing

        RowLayout {
            Layout.fillWidth: true
            spacing: Kirigami.Units.smallSpacing
            PlasmaComponents.ToolButton {
                icon.name: "go-previous"
                enabled: !addPage.busy
                PlasmaComponents.ToolTip.text: "Back"
                PlasmaComponents.ToolTip.visible: hovered
                PlasmaComponents.ToolTip.delay: Kirigami.Units.toolTipDelay
                onClicked: rootItem.addPageOpen = false
            }
            PlasmaComponents.Label {
                Layout.fillWidth: true
                text: "Add service"
                font.bold: true
            }
        }

        PlasmaComponents.Label {
            Layout.fillWidth: true
            text: "Pick a running service, or type a unit below:"
            wrapMode: Text.WordWrap
            font.pixelSize: Kirigami.Theme.smallFont.pixelSize
            color: Kirigami.Theme.disabledTextColor
        }

        PlasmaComponents.ScrollView {
            Layout.fillWidth: true
            Layout.preferredHeight: Kirigami.Units.gridUnit * 8

            ListView {
                id: candidateList
                clip: true
                model: rootItem.candidatesListModel

                delegate: Item {
                    id: candDelegate
                    width: candidateList.width
                    height: candCol.implicitHeight + Kirigami.Units.smallSpacing * 2

                    HoverHandler { id: candHover }
                    Rectangle {
                        anchors.fill: parent
                        radius: 4
                        color: Kirigami.Theme.highlightColor
                        opacity: candHover.hovered ? 0.1 : 0
                    }
                    MouseArea {
                        anchors.fill: parent
                        onClicked: {
                            unitField.text = model.template
                            memField.forceActiveFocus()
                        }
                    }
                    ColumnLayout {
                        id: candCol
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: Kirigami.Units.smallSpacing
                        spacing: 1

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Kirigami.Units.smallSpacing
                            PlasmaComponents.Label {
                                Layout.fillWidth: true
                                elide: Text.ElideMiddle
                                text: addPage.collapseUnit(model.unit)
                            }
                            PlasmaComponents.Label {
                                text: Logic.humanBytes(model.memory_current)
                                color: Kirigami.Theme.disabledTextColor
                            }
                        }
                        PlasmaComponents.Label {
                            Layout.fillWidth: true
                            visible: model.description !== ""
                            elide: Text.ElideRight
                            text: model.description
                            font.pixelSize: Kirigami.Theme.smallFont.pixelSize
                            color: Kirigami.Theme.disabledTextColor
                        }
                    }
                }

                Kirigami.PlaceholderMessage {
                    anchors.centerIn: parent
                    width: parent.width - Kirigami.Units.gridUnit * 2
                    visible: candidateList.count === 0
                    icon.name: "checkmark"
                    text: "No unlimited running services"
                }
            }
        }

        Kirigami.FormLayout {
            Layout.fillWidth: true

            PlasmaComponents.TextField {
                id: unitField
                Kirigami.FormData.label: "Unit:"
                Layout.fillWidth: true
                placeholderText: "e.g. app-foo@.service"
                enabled: !addPage.busy
            }
            PlasmaComponents.TextField {
                id: memField
                Kirigami.FormData.label: "MemoryMax:"
                Layout.fillWidth: true
                placeholderText: "e.g. 2G, 500M, max"
                enabled: !addPage.busy
            }
            PlasmaComponents.TextField {
                id: cpuField
                Kirigami.FormData.label: "CPUQuota:"
                Layout.fillWidth: true
                placeholderText: "e.g. 200%, max"
                enabled: !addPage.busy
            }
        }

        PlasmaComponents.Label {
            Layout.fillWidth: true
            visible: addPage.localError !== ""
            text: addPage.localError
            wrapMode: Text.WordWrap
            color: Kirigami.Theme.negativeTextColor
        }

        ActionFooter {
            busy: addPage.busy
            onCancel: function () { rootItem.addPageOpen = false }
            primaryText: "Save"
            // All validation (empty unit, bad MemoryMax/CPUQuota syntax,
            // unknown unit, ...) happens in cgwatch-cli; its messages[]
            // are surfaced verbatim via lastActionResult.hint above.
            // isEdit=false (plain requestApply): unlike the inline
            // editor's requestEditApply, an unknown unit here should
            // still be rejected (see cgwatch-cli's default apply path).
            onPrimary: function () {
                rootItem.requestApply(unitField.text.trim(), memField.text, cpuField.text,
                                       rootItem.addPageContextKey)
            }
        }
    }
}
